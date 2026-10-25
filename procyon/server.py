"""Web server: serves the UI and bridges it to the engine over a WebSocket.

By default Procyon only listens on this computer (127.0.0.1). When remote
access is switched on in Settings it listens on the network too, and every
device other than this computer must sign in with the PIN."""

import argparse
import asyncio
import json
import logging
import logging.handlers
import os
import shutil
import subprocess
import signal
import socket
import sys
import urllib.request
import webbrowser

from aiohttp import WSCloseCode, WSMsgType, web

from . import AUTHORS, RELEASE_DATE, __version__
from . import logimport
from .auth import COOKIE, SESSION_DAYS, is_local, trusted_host
from .engine import Engine
from .system import IS_WINDOWS, data_dir

WEB = os.path.join(os.path.dirname(__file__), "web")
log = logging.getLogger("procyon.server")


async def index(request):
    return web.FileResponse(os.path.join(WEB, "index.html"))


async def ws_handler(request):
    eng: Engine = request.app["engine"]
    local = is_local(request.remote)
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    ws.procyon_local = local
    eng.clients.add(ws)
    eng.client_joined()
    await ws.send_str(json.dumps({"type": "hello", "local": local}))
    await ws.send_str(json.dumps(eng.state()))
    await ws.send_str(json.dumps({"type": "history", "batches": list(eng.history)}))
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                reply = await eng.command(json.loads(msg.data), local=local)
                if reply:
                    await ws.send_str(json.dumps(reply))
            except Exception as e:  # noqa: BLE001
                logging.exception("command")
                await ws.send_str(json.dumps({"type": "toast", "text": str(e), "kind": "bad"}))
    finally:
        eng.clients.discard(ws)
        eng.client_left()
    return ws


async def download_log(request):
    eng: Engine = request.app["engine"]
    if eng.s["demo"]:   # the practice log only ever leaves as plain text, never ADIF
        return web.Response(text=eng.practice_log_text(), content_type="text/plain", headers={
            "Content-Disposition": 'attachment; filename="Procyon-DEMO-practice-log.txt"'})
    if eng.s["export_new_only"] and "all" not in request.query:
        name, text, n = eng.export_new()
        asyncio.get_event_loop().create_task(eng.push_state())   # the button's count changes
        if not n:
            return web.Response(status=204)   # nothing new: no empty file to import
        return web.Response(text=text, content_type="application/octet-stream", headers={
            "Content-Disposition": f'attachment; filename="{name}"'})
    if not os.path.exists(eng.logbook.path):
        raise web.HTTPNotFound()
    return web.FileResponse(eng.logbook.path, headers={
        "Content-Disposition": f"attachment; filename={os.path.basename(eng.logbook.path)}"})


async def download_pota(request):
    eng: Engine = request.app["engine"]
    got = eng.pota_export(request.match_info["park"])
    if not got:
        raise web.HTTPNotFound()
    name, text = got
    return web.Response(text=text, content_type="application/octet-stream", headers={
        "Content-Disposition": f'attachment; filename="{name}"'})


# ------------------------------------------------------------ sign-in

async def login_page(request):
    return web.FileResponse(os.path.join(WEB, "login.html"))


async def login(request):
    eng: Engine = request.app["engine"]
    ip = request.remote or "?"
    wait = eng.auth.locked_for(ip)
    if wait:
        return web.json_response({"ok": False, "wait": wait}, status=429)
    try:
        pin = str((await request.json()).get("pin", ""))
    except Exception:  # noqa: BLE001
        pin = ""
    pin_hash = eng.s["remote_pin_hash"]
    if not eng.auth.check_pin(ip, pin, pin_hash):
        log.warning("wrong PIN from %s", ip)
        return web.json_response({"ok": False, "wait": eng.auth.locked_for(ip),
                                  "left": eng.auth.tries_left(ip)}, status=401)
    log.info("device %s signed in", ip)
    eng.toast(f"A device at {ip} signed in to Procyon", "info")
    resp = web.json_response({"ok": True})
    resp.set_cookie(COOKIE, eng.auth.token(pin_hash), max_age=SESSION_DAYS * 86400,
                    httponly=True, samesite="Strict", path="/")
    return resp


IMPORT_HEADER = "X-Procyon-Import"   # only Procyon's own page sends it (see import_adif)
IMPORT_MAX = 64 * 1024 * 1024        # bytes: far more than any ham's logbook


async def import_adif(request):
    """An ADIF file picked in Settings → Advanced, merged into the log.
    Only from this computer, and only from Procyon's own page: another web
    site can't add the custom header without the browser asking us first
    (which we never allow), so it can't slip QSOs into the log."""
    eng: Engine = request.app["engine"]
    if not is_local(request.remote) or request.headers.get(IMPORT_HEADER) != "1":
        return web.json_response({"type": "log_import", "ok": False,
                                  "text": "That can only be done on the computer running Procyon."}, status=403)
    if eng.importing:
        return web.json_response({"type": "log_import", "ok": False,
                                  "text": "An import is already running - wait for it to finish."})
    data = await request.read()
    text = data.decode("utf-8", errors="replace")
    name = os.path.basename(request.query.get("name", "")) or "the file"
    reply = eng.import_qsos(logimport.parse_adif(text), name)
    asyncio.get_event_loop().create_task(eng.push_state())
    return web.json_response(reply)


async def logout(request):
    resp = web.HTTPFound("/login")
    resp.del_cookie(COOKIE, path="/")
    raise resp


@web.middleware
async def own_page_only(request, handler):
    """Only Procyon's own page may talk to Procyon. Without this, any web site
    open in a browser on this computer could connect in the background and
    send commands - even "call CQ" or "enable TX".
    - The address must be this computer's (see auth.trusted_host).
    - The live connection (/ws) and anything that changes something (POST)
      must come from Procyon's own page: browsers always say which page with
      the Origin header. Tools that aren't browsers send none: allowed."""
    host = request.host.rsplit(":", 1)[0] if not request.host.endswith("]") else request.host
    if not trusted_host(host):
        logging.warning("refused a request for %r (not this computer's address)", request.host)
        return web.Response(status=403, text="Procyon only answers at this computer's own address, "
                                             "e.g. http://127.0.0.1:8073/ or the address shown in Settings.")
    origin = request.headers.get("Origin")
    if origin and (request.path == "/ws" or request.method not in ("GET", "HEAD")):
        if origin.rstrip("/").lower() != f"{request.scheme}://{request.host}".lower():
            logging.warning("refused %s %s from another web page (%s)", request.method, request.path, origin)
            return web.Response(status=403, text="Only Procyon's own page can do that.")
    return await handler(request)


@web.middleware
async def guard(request, handler):
    """This computer is always allowed. Other devices need remote access
    switched on and a valid sign-in."""
    if is_local(request.remote):
        return await handler(request)
    eng: Engine = request.app["engine"]
    if not eng.s["remote_enabled"]:
        return web.Response(status=403, text="Remote access to Procyon is switched off.")
    if request.path in ("/login", "/logout"):
        return await handler(request)
    if eng.auth.valid_session(request.cookies.get(COOKIE), eng.s["remote_pin_hash"]):
        return await handler(request)
    if request.path == "/":
        raise web.HTTPFound("/login")
    return web.Response(status=401, text="Sign in first.")


@web.middleware
async def no_cache(request, handler):
    resp = await handler(request)
    if not isinstance(resp, web.WebSocketResponse):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


class Listener:
    """Owns the listening socket, so remote access can be switched on and
    off without restarting Procyon."""

    def __init__(self, runner, port, host_override=None):
        self.runner, self.port, self.override = runner, port, host_override
        self.site = None
        self.host = None

    async def apply(self, remote: bool):
        host = self.override or ("0.0.0.0" if remote else "127.0.0.1")
        if host == self.host:
            return
        if self.site:
            await self.site.stop()
        self.site = web.TCPSite(self.runner, host, self.port)
        await self.site.start()
        self.host = host
        log.info("listening on %s:%d%s", host, self.port,
                 " (network devices allowed with PIN)" if host != "127.0.0.1" else "")


def migrate_old_data(new_dir: str):
    """Procyon was called Skywave at first: bring its settings, logbooks and
    remembered grids across (moved, not copied - nothing is deleted)."""
    old_dir = os.path.join(os.path.dirname(new_dir), "skywave")
    if os.path.exists(new_dir) or not os.path.isdir(old_dir):
        return
    os.rename(old_dir, new_dir)
    for name in os.listdir(new_dir):
        if name.startswith("skywave_"):
            os.rename(os.path.join(new_dir, name), os.path.join(new_dir, "procyon_" + name[len("skywave_"):]))
    print(f"  Moved your Skywave settings and logbook to {new_dir}")


def procyon_running(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return b"<title>Procyon" in r.read(4096)
    except OSError:
        return False


def port_free(host, port) -> bool:
    with socket.socket() as probe:
        if os.name != "nt":
            # Like the server itself: ignore connections still closing from a
            # previous run (TIME_WAIT), only catch a copy that's really running.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
            return True
        except OSError:
            return False


def main():
    ap = argparse.ArgumentParser(description="Procyon - a friendly FT8 station")
    ap.add_argument("--version", action="version",
                    version=f"Procyon {__version__} ({RELEASE_DATE}) - created by {AUTHORS}")
    ap.add_argument("--port", type=int, default=8073)
    ap.add_argument("--host", default=None,
                    help="address to listen on (default: this computer only, or the "
                         "whole network when remote access is on in Settings)")
    ap.add_argument("--demo", action="store_true", help="simulate a busy band (no radio needed)")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--data", default=data_dir(),
                    help="settings and logbook folder")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    # Started from the menu there's no terminal window, so keep a log file too
    # (and say where it is if Procyon can't start).
    log_path = os.path.join(args.data, "procyon.log")
    where = f"Details are in {log_path}"
    try:
        os.makedirs(args.data, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=2,
                                                  encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(name)s: %(message)s"))
        logging.getLogger().addHandler(fh)
    except OSError as e:
        log.warning("can't write the log file %s: %s", log_path, e)
        where = "Start Procyon from a terminal (run: procyon) to see the details"

    log.info("Procyon %s starting (data folder %s)", __version__, args.data)
    try:
        _run(args)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 - say so even when nobody sees a terminal
        log.exception("Procyon couldn't start")
        tell(f"Procyon couldn't start: {e}\n{where}", problem=True)
        sys.exit(1)


def tell(text: str, problem=False):
    """Something the user must know: printed for a terminal, and as a desktop
    notification when there's no terminal (started from the menu)."""
    print(f"\n  {text}\n", flush=True)
    log.warning(text) if problem else log.info(text)
    notify = shutil.which("notify-send")
    if notify and not (sys.stdout and sys.stdout.isatty()):
        try:
            subprocess.Popen([notify, "--app-name=Procyon", "--icon=procyon",
                              *(["--urgency=critical"] if problem else []), "Procyon", text],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass


def _run(args):
    default_data = data_dir()
    if os.path.abspath(args.data) == default_data and not IS_WINDOWS:
        migrate_old_data(default_data)
    eng = Engine(args.data, force_demo=args.demo)
    eng.port = args.port
    first_host = args.host or ("0.0.0.0" if eng.s["remote_enabled"] else "127.0.0.1")
    # Check the port first, so a second copy explains itself instead of crashing.
    if not port_free(first_host, args.port):
        url = f"http://127.0.0.1:{args.port}/"
        if procyon_running(url):
            # Launched again from the menu: just bring up the copy that's running.
            print(f"\n  Procyon is already running - opening {url}\n")
            if not args.no_browser:
                webbrowser.open(url)
            sys.exit(0)
        tell(f"Procyon can't start: port {args.port} is already in use by another program.\n"
             f"If Procyon is already running, open http://127.0.0.1:{args.port}/ - otherwise "
             f"close the other program, or start Procyon with --port {args.port + 1}.", problem=True)
        sys.exit(1)

    async def on_shutdown(app):
        # Close browser connections now, or the server waits up to a minute
        # for open tabs before it exits on Ctrl-C.
        for ws in list(app["engine"].clients):
            await ws.close(code=WSCloseCode.GOING_AWAY, message=b"Procyon is shutting down")

    async def on_cleanup(app):
        e = app["engine"]
        await e.shutdown()     # transmit off and radio on receive, before anything closes
        await e.udp.stop()     # tells GridTracker etc. that Procyon has closed
        await e.rig.close()
        if e.rx_task:
            e.rx_task.cancel()
        if e.rx_task:   # let the audio capture close fully before the loop stops
            try:
                await asyncio.wait_for(e.rx_task, 2)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):  # noqa: BLE001
                pass   # cancelled, as intended

    app = web.Application(middlewares=[own_page_only, guard, no_cache], client_max_size=IMPORT_MAX)
    app["engine"] = eng
    app.router.add_get("/", index)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/log.adi", download_log)
    app.router.add_get("/pota/{park}.adi", download_pota)
    app.router.add_post("/import.adi", import_adif)
    app.router.add_get("/login", login_page)
    app.router.add_post("/login", login)
    app.router.add_get("/logout", logout)
    app.router.add_static("/static", WEB)
    app.on_shutdown.append(on_shutdown)
    app.on_cleanup.append(on_cleanup)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    runner = web.AppRunner(app)

    async def start():
        await runner.setup()
        await eng.start()
        listener = Listener(runner, args.port, args.host)
        eng.on_remote_change = listener.apply

        def quit_procyon():
            print("\n  Quit pressed - Procyon is stopping.", flush=True)
            loop.call_later(0.3, loop.stop)
        eng.on_quit = quit_procyon
        await listener.apply(eng.s["remote_enabled"])
        url = f"http://127.0.0.1:{args.port}/"
        print(f"\n  Procyon {__version__} is running at {url}")
        log.info("running at %s", url)
        if listener.host != "127.0.0.1":
            for u in eng.state()["remote"]["urls"]:
                print(f"  Other devices on your network: {u}  (PIN required)")
        print(flush=True)
        if not args.no_browser:
            loop.call_later(0.5, webbrowser.open, url)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, loop.stop)
        except (NotImplementedError, RuntimeError):
            pass  # Windows: Ctrl-C arrives as KeyboardInterrupt instead
    try:
        loop.run_until_complete(start())
        loop.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        loop.run_until_complete(runner.cleanup())
        # Stop the engine's background jobs (audio, scheduler) cleanly.
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        for t in pending:
            t.cancel()
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()


if __name__ == "__main__":
    main()
