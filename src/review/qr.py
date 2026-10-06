"""A QR code for the authenticator app's otpauth:// address, drawn here (no image service, no added package).

QR Code Model 2 (ISO/IEC 18004), byte mode, error correction level M, versions 1 to 20 (up to 666 bytes; the
address is about 200). The steps and tables follow Project Nayuki's reference "QR Code generator library"
(https://www.nayuki.io/page/qr-code-generator-library, MIT License, read 10/03/2026): the data bits, Reed-Solomon
error correction over GF(2^8) with the polynomial 0x11D, the blocks interleaved, the function patterns (finders,
timing, alignment, format and version bits), the zigzag placement, and the mask with the lowest penalty of the
eight. tests/test_totp.py reads each code back with the zxing-cpp decoder the document readers already use.

svg() gives the picture: black squares on white with the four-module quiet zone the standard asks for.

Portions follow the QR Code generator library (Python):
  Copyright (c) Project Nayuki. (MIT License)
  https://www.nayuki.io/page/qr-code-generator-library
  Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
  documentation files (the "Software"), to deal in the Software without restriction, including without limitation the
  rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit
  persons to whom the Software is furnished to do so, subject to the following conditions:
  - The above copyright notice and this permission notice shall be included in all copies or substantial portions of
    the Software.
  - The Software is provided "as is", without warranty of any kind, express or implied, including but not limited to
    the warranties of merchantability, fitness for a particular purpose and noninfringement. In no event shall the
    authors or copyright holders be liable for any claim, damages or other liability, whether in an action of
    contract, tort or otherwise, arising from, out of or in connection with the Software or the use or other dealings
    in the Software.
"""

from __future__ import annotations

# Level M, indexed by version (index 0 unused): error-correction codewords per block, and the number of blocks.
_ECC_PER_BLOCK = (-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26)
_BLOCKS = (-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16)
_FORMAT_M = 0  # the two format bits for level M
MAX_VERSION = 20


def _raw_modules(ver: int) -> int:
    """Modules left for data and error correction once the function patterns are drawn."""
    result = (16 * ver + 128) * ver + 64
    if ver >= 2:
        align = ver // 7 + 2
        result -= (25 * align - 10) * align - 55
        if ver >= 7:
            result -= 36
    return result


def _data_codewords(ver: int) -> int:
    return _raw_modules(ver) // 8 - _ECC_PER_BLOCK[ver] * _BLOCKS[ver]


def _gf_mul(x: int, y: int) -> int:
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree: int) -> list[int]:
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _gf_mul(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_mul(root, 0x02)
    return result


def _rs_remainder(data: list[int], divisor: list[int]) -> list[int]:
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _gf_mul(coef, factor)
    return result


def _codewords(data: bytes) -> tuple[int, list[int]]:
    """(version, every codeword in the order it is placed): the bits, padded, split into blocks, each with its error
    correction, interleaved."""
    for ver in range(1, MAX_VERSION + 1):
        count_bits = 8 if ver <= 9 else 16
        if 4 + count_bits + 8 * len(data) <= _data_codewords(ver) * 8:
            break
    else:
        raise ValueError("Too long for a QR code here.")
    bits: list[int] = []

    def put(value: int, n: int) -> None:
        bits.extend((value >> i) & 1 for i in reversed(range(n)))

    put(0b0100, 4)  # byte mode
    put(len(data), count_bits)
    for b in data:
        put(b, 8)
    capacity = _data_codewords(ver) * 8
    put(0, min(4, capacity - len(bits)))  # terminator
    put(0, -len(bits) % 8)
    pad = 0xEC
    while len(bits) < capacity:
        put(pad, 8)
        pad ^= 0xEC ^ 0x11
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]

    blocks_n, ecc_len = _BLOCKS[ver], _ECC_PER_BLOCK[ver]
    raw = _raw_modules(ver) // 8
    short_n, short_len = blocks_n - raw % blocks_n, raw // blocks_n
    divisor = _rs_divisor(ecc_len)
    blocks, k = [], 0
    for i in range(blocks_n):
        dat = words[k:k + short_len - ecc_len + (0 if i < short_n else 1)]
        k += len(dat)
        ecc = _rs_remainder(dat, divisor)
        if i < short_n:
            dat = dat + [0]  # a placeholder, skipped when interleaving
        blocks.append(dat + ecc)
    out = []
    for i in range(len(blocks[0])):
        for j, block in enumerate(blocks):
            if i != short_len - ecc_len or j >= short_n:
                out.append(block[i])
    return ver, out


def _alignment_positions(ver: int, size: int) -> list[int]:
    if ver == 1:
        return []
    align = ver // 7 + 2
    step = (ver * 8 + align * 3 + 5) // (align * 4 - 4) * 2
    return list(reversed([size - 7 - i * step for i in range(align - 1)] + [6]))


def _mask_bit(mask: int, x: int, y: int) -> bool:
    return (((x + y) % 2 == 0), (y % 2 == 0), (x % 3 == 0), ((x + y) % 3 == 0), ((x // 3 + y // 2) % 2 == 0),
            (x * y % 2 + x * y % 3 == 0), ((x * y % 2 + x * y % 3) % 2 == 0), (((x + y) % 2 + x * y % 3) % 2 == 0))[mask]


class _Grid:
    def __init__(self, ver: int):
        self.ver, self.size = ver, ver * 4 + 17
        self.dark = [[False] * self.size for _ in range(self.size)]
        self.function = [[False] * self.size for _ in range(self.size)]

    def set(self, x: int, y: int, dark: bool) -> None:
        self.dark[y][x], self.function[y][x] = dark, True

    def patterns(self) -> None:
        size = self.size
        for i in range(size):  # timing
            self.set(6, i, i % 2 == 0)
            self.set(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):  # finders, with their separators
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < size and 0 <= y < size:
                        self.set(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        pos = _alignment_positions(self.ver, size)
        last = len(pos) - 1
        for i, ax in enumerate(pos):
            for j, ay in enumerate(pos):
                if (i, j) in ((0, 0), (0, last), (last, 0)):
                    continue  # under a finder
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.set(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)
        self.format_bits(0)  # reserved now, written once the mask is chosen
        if self.ver >= 7:
            rem = self.ver
            for _ in range(12):
                rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
            bits = self.ver << 12 | rem
            for i in range(18):
                bit = (bits >> i) & 1 == 1
                a, b = size - 11 + i % 3, i // 3
                self.set(a, b, bit)
                self.set(b, a, bit)

    def format_bits(self, mask: int) -> None:
        data = _FORMAT_M << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412
        bit = lambda i: (bits >> i) & 1 == 1  # noqa: E731
        size = self.size
        for i in range(6):
            self.set(8, i, bit(i))
        self.set(8, 7, bit(6))
        self.set(8, 8, bit(7))
        self.set(7, 8, bit(8))
        for i in range(9, 15):
            self.set(14 - i, 8, bit(i))
        for i in range(8):
            self.set(size - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self.set(8, size - 15 + i, bit(i))
        self.set(8, size - 8, True)  # the dark module

    def place(self, words: list[int]) -> None:
        size, i, total = self.size, 0, len(words) * 8
        right = size - 1
        while right >= 1:
            if right == 6:
                right = 5  # the vertical timing column
            for vert in range(size):
                for j in range(2):
                    x = right - j
                    y = size - 1 - vert if ((right + 1) & 2) == 0 else vert
                    if not self.function[y][x] and i < total:
                        self.dark[y][x] = (words[i >> 3] >> (7 - (i & 7))) & 1 == 1
                        i += 1
            right -= 2

    def apply_mask(self, mask: int) -> None:
        for y in range(self.size):
            for x in range(self.size):
                if not self.function[y][x] and _mask_bit(mask, x, y):
                    self.dark[y][x] = not self.dark[y][x]

    def penalty(self) -> int:
        """The standard's four rules: runs of five or more, 2x2 blocks, finder-like 1:1:3:1:1 runs beside four light
        modules, and how far the dark share is from half."""
        size, grid, score = self.size, self.dark, 0
        lines = [row for row in grid] + [[grid[y][x] for y in range(size)] for x in range(size)]
        finder = ([True, False, True, True, True, False, True, False, False, False, False],
                  [False, False, False, False, True, False, True, True, True, False, True])
        for line in lines:
            run = 1
            for k in range(1, size + 1):
                if k < size and line[k] == line[k - 1]:
                    run += 1
                    continue
                if run >= 5:
                    score += 3 + (run - 5)
                run = 1
            for k in range(size - 10):
                window = line[k:k + 11]
                if window == finder[0] or window == finder[1]:
                    score += 40
        for y in range(size - 1):
            for x in range(size - 1):
                if grid[y][x] == grid[y][x + 1] == grid[y + 1][x] == grid[y + 1][x + 1]:
                    score += 3
        dark = sum(sum(row) for row in grid)
        total = size * size
        score += ((abs(dark * 20 - total * 10) + total - 1) // total - 1) * 10
        return score


def matrix(text: str) -> list[list[bool]]:
    """The modules of the QR code for text (True = dark), the mask with the lowest penalty."""
    ver, words = _codewords(text.encode("utf-8"))
    best, best_score = None, None
    for mask in range(8):
        grid = _Grid(ver)
        grid.patterns()
        grid.place(words)
        grid.apply_mask(mask)
        grid.format_bits(mask)
        score = grid.penalty()
        if best_score is None or score < best_score:
            best, best_score = grid, score
    return best.dark


def svg(text: str, quiet: int = 4) -> str:
    """The QR code as an SVG picture, one path of unit squares, scaled by the page."""
    grid = matrix(text)
    n = len(grid) + 2 * quiet
    path = "".join(f"M{x + quiet},{y + quiet}h1v1h-1z" for y, row in enumerate(grid) for x, dark in enumerate(row) if dark)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" shape-rendering="crispEdges">'
            f'<rect width="{n}" height="{n}" fill="#fff"/><path d="{path}" fill="#000"/></svg>')
