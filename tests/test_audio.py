"""Sound devices: Procyon must use the device you picked, or nothing.

1.0.1 (bug report #1): a device name the sound server didn't know made it
quietly use another device - transmit audio came out of the computer's
speaker. And the WSJT-X import kept Qt's quote marks around names."""

import asyncio
import os

from _common import scratch_dir

from procyon import setup_help
from procyon.audio import match_device
from test_engine import _engine

USB_OUT = "alsa_output.usb-C-Media_Electronics_Inc._USB_Audio_Device-00.analog-stereo"
USB_IN = "alsa_input.usb-C-Media_Electronics_Inc._USB_Audio_Device-00.mono-fallback"


def test_device_names_match_exactly_or_not_at_all():
    names = [USB_IN, "alsa_input.pci-0000_09_00.4.analog-stereo"]
    assert match_device(USB_IN, names) == USB_IN
    assert match_device("", names) == ""                       # "System default" is a choice
    assert match_device(USB_IN + ".9", names) == USB_IN         # PipeWire's numbered copy
    assert match_device("plughw:CARD=AUDIO,DEV=0", names) is None
    assert match_device(USB_OUT.replace("analog", "iec958"), [USB_OUT]) is None   # other profile


class FakeSound:
    """Knows one input and one output; records what was played where."""
    name = "fake"

    def __init__(self):
        self.played = []

    def inputs(self):
        return [{"name": USB_IN, "desc": "USB in"}]

    def outputs(self):
        return [{"name": USB_OUT, "desc": "USB out"}]

    def resolve(self, name, kind):
        devs = self.inputs() if kind == "in" else self.outputs()
        return match_device(name, [d["name"] for d in devs])

    async def play(self, device, *a, **k):
        self.played.append(device)


class SpyRig:
    enabled = True
    error = ""

    def __init__(self):
        self.ptt_calls = []

    async def ptt(self, on):
        self.ptt_calls.append(on)
        return True


def _setup(audio_out):
    e, _ = _engine()
    e.audio, e.rig = FakeSound(), SpyRig()
    e.s.update(audio_out=audio_out, ptt="cat")
    return e


def test_a_missing_output_never_keys_the_radio():
    e = _setup("plughw:CARD=AUDIO,DEV=0")
    try:
        asyncio.run(e._play(lambda rate: [], 0))
        raise AssertionError("should have refused")
    except RuntimeError as err:
        assert "isn't connected" in str(err)
    assert e.rig.ptt_calls == [] and e.audio.played == []


def test_the_right_output_is_played_and_ptt_released():
    e = _setup(USB_OUT + ".3")
    asyncio.run(e._play(lambda rate: [], 0))
    assert e.audio.played == [USB_OUT] and e.rig.ptt_calls == [True, False]


def test_a_missing_input_is_reported_not_swapped():
    e, _ = _engine()
    e.audio = FakeSound()
    e.s["audio_in"] = "no_such_input"
    assert e._device("in") is None
    e.s["audio_in"] = USB_IN + ".9"
    assert e._device("in") == USB_IN


def test_wsjtx_import_removes_qt_quotes():
    folder = scratch_dir("wsjtx")
    ini = os.path.join(folder, "WSJT-X.ini")
    with open(ini, "w") as f:
        f.write('[Configuration]\nMyCall=K1ABC\nSoundInName="plughw:CARD=AUDIO,DEV=0"\n'
                'SoundOutName="say \\"hi\\", ok"\n')
    old = setup_help.wsjtx_ini_path
    setup_help.wsjtx_ini_path = lambda: ini
    try:
        s, _ = setup_help.import_wsjtx()
    finally:
        setup_help.wsjtx_ini_path = old
    assert s["audio_in"] == "plughw:CARD=AUDIO,DEV=0"
    assert s["audio_out"] == 'say "hi", ok'
    assert s["mycall"] == "K1ABC"
