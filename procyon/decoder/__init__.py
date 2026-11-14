"""Procyon's own FT8/FT4 decoder (in place of WSJT-X's jt9 since 0.9.69).

    ft8search.decode_cycle(audio, ...)   one 15 s FT8 cycle -> decodes
    ft4search.decode_cycle(audio, ...)   one 7.5 s FT4 cycle

Written for Procyon from the published FT8/FT4 descriptions; ideas checked
against WSJT-X's and JTDX's (GPLv3) source - the re-read of failed
candidates next to a subtracted signal (NEAR_RETRY) is JTDX's idea.
The lab copy with the test bench lives outside the app (decoder-lab branch).
"""
