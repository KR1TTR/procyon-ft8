"""Procyon's own JS8 modem (work in progress on the js8 branch, for 1.1).

    js8msg.build_frames(mycall, grid, text)   typed text -> frames
    js8enc.tones(frame, flags, speed)         frame -> 79 tones
    js8enc.synth(tones, hz, speed)            tones -> audio
    js8dec.decode(audio, speed)               one slot of audio -> frames
    js8msg.decode_message(frames)             frames -> the text JS8Call shows

Ported from JS8Call 2.2.0 (GPLv3, Jordan Sherer KN4CRD, building on WSJT-X
by Joe Taylor K1JT et al.). The test bench (recordings, scoring, the live
listen-only helper and the chat-screen prototype) lives outside the app in
js8-chat/.
"""
