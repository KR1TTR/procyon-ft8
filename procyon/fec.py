"""FT8/FT4 error correction: the 14-bit CRC and the LDPC(174,91) parity
bits that protect the 77 message bits. FT8 and FT4 share the same code
(port of WSJT-X's encode174_91; GPLv3)."""

import numpy as np

# LDPC parity: row i gives which of the 77 message bits are XORed into parity
# bit i (77 bits + 3 zero bits, as hex). The CRC is folded in: this is WSJT-X's
# (174,91) generator times the CRC, worked out from ft8code's own output and
# checked against it (550 messages of every type).
_PARITY_HEX = (
    "13521879b41f43a39468", "66aba6f76f05360be7e8", "ac839acf7f19ab34cc30",
    "adf4ac5057945e064428", "5c0afef62c4a462cdd88", "13b1a967b283ece178c8",
    "4c74408982e0ada71ee8", "c30e66bd90fbd5e00398", "82815ff88a65b8314a38",
    "006c27c3c70083c0a588", "d953b183730ac0349190", "48cb67d29be2a2b789b0",
    "8ceb0cb3f5b6a72832f0", "57d921f43b24db868ca8", "1ccccea63f8d44bcd5d8",
    "2a36c2c78c16a0d56058", "a0e7f567f61ad81f2c30", "8a13768f68a3bab14f90",
    "bd6b89d09172490f3ce8", "d7b46de62f3c65fda7d8", "df3d97532f03a3d96678",
    "1ef4ecdd98290d007f90", "97fe3ef2b09aeb085618", "01cecdfc2a4844d39780",
    "7ef65aae8078cb3296b8", "c68b351cca199d7c0658", "210c329383012f60e298",
    "56ecc8e8629152fb3b60", "c986504b903150b760b8", "359ad66bdcf4b82d63d0",
    "0ba4dcd9dad3b436c4a0", "043461fc77c417003740", "8c23f6f5c7d69c8f5508",
    "24d46bead0a5856b5f68", "a8f79d3f167318e40a60", "e04ae51ae7ee3c976100",
    "965e5639d6006cd638e8", "f1a76c4a3871adac66c0", "1c54dbc7847216ac4258",
    "3ed293b3821a85a490b8", "c01a3878d6745395d7a8", "41cecddc2ac844df9780",
    "8bd924585aa0eda3f638", "71afc6b439c7415b6a68", "353c48f50ece6772af30",
    "fbdf9425ead752d8cf90", "bf463459a4f0f40472a8", "aceb0db7f5b6a72833f0",
    "8ccaf30853fb5d437b58", "684c4f83b812b9c96b88", "5061950b015fdb874098",
    "f3fe52a6fbde182e99a0", "77f0fa7be1903d93d9f8", "ba786a8222723896bad8",
    "6f656b0a454d72c87068", "b0c803c4ce349a149d80", "e25f394cd03103782410",
    "13f85406e840bed442c0", "25ab1bfb5ef209b703d8", "a3274716a5ca1f0a0890",
    "55b97a0b94e4a2dee348", "4cc97dd5d8f797a3af30", "e78a4fe26ea744748dd8",
    "8e9a90774c2e1bb20d38", "288a516c82952b79c4a0", "b6fa0c61339be469b480",
    "54e5b30b77a34e7e9040", "18faaf5a4809539ce298", "c8aabae44e2861d64738",
    "99bfc23a61d7cc446300", "5b3b33d471a21c63c370", "f21d761d71ce6c1aa440",
    "c146d38964ed13b78398", "ac143c2010edb7a7e2c0", "88d15fbb4d96f1225f18",
    "e46ff9767fc4a7463e80", "e3ead3e470a655e2ede0", "eaff4652381456da9160",
    "e3dae6fedbf2fb363e88", "0e4ba4f0d99ee1723168", "127d0ab4e3c3569a5868",
    "525f741bca91bd148680", "bf89a4e4b4570a9c3c40",
)

_PARITY = np.array([[int(c) for c in format(int(h, 16), "080b")[:77]]
                    for h in _PARITY_HEX], dtype=np.uint8)


def crc14(bits) -> list[int]:
    """WSJT-X's 14-bit CRC (polynomial 0x2757) of 77 bits, zero-padded to 96."""
    reg = 0
    for b in list(bits) + [0] * 19:
        reg = (reg << 1) | int(b)
        if reg & 0x4000:
            reg ^= 0x6757
    return [int(c) for c in format(reg, "014b")]


def codeword(bits77) -> list[int]:
    """The 174-bit LDPC codeword: message bits, CRC, parity."""
    b = np.asarray(bits77, dtype=np.uint8)
    parity = (_PARITY.astype(int) @ b) % 2
    return list(map(int, b)) + crc14(b) + list(map(int, parity))
