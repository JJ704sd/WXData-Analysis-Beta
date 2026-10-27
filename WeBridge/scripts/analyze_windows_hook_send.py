"""Bounded disk-only x64 PE send-chain analysis. Never opens a process or invokes code.

Byte-pattern references are confirmed against an instruction-aligned objdump
listing before being classified as confirmed. Semantic/ABI and delivery claims
remain unverified. The optional reference image is inspected, never loaded.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess


ANCHORS = (
    "/cgi-bin/micromsg-bin/newsendmsg", "TextMessageHandler",
    "Begin StartSendMessageSyncStage", "SaveSendMessagesAtOnce",
    "GetAddSendMessageToDb", "micromsg.SendMsgRequestNew",
    "micromsg.MicroMsgRequestNew",
)
LEGACY = {
    "create_param2": 0xDF40, "send_message": 0x1677A30,
    "param1_vtable": 0x84EC9C8, "param2_global": 0xA0CE0B0,
    "param2_1": 0x8595F58, "param2_2": 0x8595E98, "param2_3": 0x8595DD8,
    "text_constructor": 0x6B2C30, "text_vtable": 0x8279358,
    "newsendmsg_request": 0x36D3060, "outer_copy": 0x2C6D230,
    "outer_parser": 0x2C6CF10, "repeat_copy": 0x2C72DE0,
    "element_copy": 0x2C6C060, "element_vtable": 0x87A8038,
}


class Image:
    def __init__(self, path):
        self.path = Path(path)
        self.data = self.path.read_bytes()
        d = self.data
        if d[:2] != b"MZ":
            raise ValueError("not a PE image")
        pe = struct.unpack_from("<I", d, 0x3C)[0]
        if d[pe:pe+4] != b"PE\0\0":
            raise ValueError("invalid PE signature")
        machine, count = struct.unpack_from("<HH", d, pe+4)
        optional_size = struct.unpack_from("<H", d, pe+20)[0]
        op = pe+24
        if machine != 0x8664 or struct.unpack_from("<H", d, op)[0] != 0x20B:
            raise ValueError("requires x64 PE32+")
        self.base = struct.unpack_from("<Q", d, op+24)[0]
        self.sections = []
        for n in range(count):
            off = op+optional_size+n*40
            name = d[off:off+8].rstrip(b"\0").decode("ascii")
            virtual_size, rva, size, raw = struct.unpack_from("<IIII", d, off+8)
            flags = struct.unpack_from("<I", d, off+36)[0]
            self.sections.append(dict(name=name, rva=rva, size=size, raw=raw,
                                      virtual_size=virtual_size, executable=bool(flags & 0x20000000)))
        rva, size = struct.unpack_from("<II", d, op+112+3*8)
        raw = self.offset(rva)
        self.functions = [struct.unpack_from("<III", d, p) for p in range(raw, raw+size, 12)]
        self.functions.sort()
        self.starts = [r[0] for r in self.functions]

    def section(self, rva):
        return next((s for s in self.sections if s["rva"] <= rva < s["rva"]+s["size"]), None)

    def offset(self, rva):
        s = self.section(rva)
        if s is None:
            raise ValueError("RVA not file backed")
        return s["raw"]+rva-s["rva"]

    def function(self, rva):
        i = bisect_right(self.starts, rva)-1
        if i >= 0:
            row = self.functions[i]
            if row[0] <= rva < row[1]:
                return row
        return None

    def occurrences(self, needle, executable=False):
        for s in self.sections:
            if s["executable"] != executable:
                continue
            block = self.data[s["raw"]:s["raw"]+s["size"]]
            pos = 0
            while (pos := block.find(needle, pos)) >= 0:
                yield s["rva"]+pos
                pos += 1

    def pointer_refs(self, rva):
        return list(self.occurrences(struct.pack("<Q", self.base+rva)))


def disassemble(image, begin, end, objdump):
    result = subprocess.run(
        [objdump, "-d", "-Mintel", f"--start-address={image.base+begin:#x}",
         f"--stop-address={image.base+end:#x}", str(image.path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, check=True,
    )
    instructions = {}
    for line in result.stdout.splitlines():
        match = re.match(r"\s*([0-9a-f]+):\s+((?:[0-9a-f]{2}\s+)+)\s*(\S.*)?$", line)
        if match and match[3]:
            instructions[int(match[1], 16)-image.base] = match[3]
    return instructions, result.stdout


def analyze(image, objdump, extra_functions=()):
    anchors = {name: list(image.occurrences(name.encode("ascii"))) for name in ANCHORS}
    targets = {r+delta: name for name, rows in anchors.items() for r in rows for delta in range(len(name))}
    refs = []
    # SSE constant copies matter: this compiler inlines std::string literals as
    # MOVUPS instead of taking their address with LEA. Match byte candidates,
    # then disassemble from the .pdata function start to reject false alignment.
    patterns = (
        re.compile(rb"[\x40-\x4f][\x8d\x8b][\x05\x0d\x15\x1d\x25\x2d\x35\x3d]"),
        re.compile(rb"\x0f[\x10\x28\x6f][\x05\x0d\x15\x1d\x25\x2d\x35\x3d]"),
    )
    for section in image.sections:
        if not section["executable"]:
            continue
        block = image.data[section["raw"]:section["raw"]+section["size"]]
        for pattern in patterns:
            for match in pattern.finditer(block):
                pos = match.start()
                if pos+7 > len(block):
                    continue
                rva = section["rva"]+pos
                target = rva+7+struct.unpack_from("<i", block, pos+3)[0]
                if target in targets:
                    fn = image.function(rva)
                    refs.append(dict(anchor=targets[target], instruction_rva=hex(rva),
                                     data_rva=hex(target), function_rva=hex(fn[0]) if fn else None))
    starts = {int(r["function_rva"], 16) for r in refs if r["function_rva"]}
    starts.update(extra_functions)
    functions, listings = {}, {}
    for start in sorted(starts):
        function = image.function(start)
        if not function:
            continue
        begin, end, unwind = function
        if end-begin > 0x20000:
            continue
        insns, listing = disassemble(image, begin, end, objdump)
        listings[f"{begin:08x}"] = listing
        calls = []
        for address, mnemonic in insns.items():
            m = re.match(r"(call|jmp)\s+0x([0-9a-f]+)(?:\s|$)", mnemonic)
            if m:
                calls.append(dict(instruction_rva=hex(address), opcode=m[1],
                                  target_rva=hex(int(m[2], 16)-image.base)))
        functions[hex(begin)] = dict(begin_rva=hex(begin), end_rva=hex(end), unwind_rva=hex(unwind),
                                    direct_calls_or_jumps=calls,
                                    absolute_pointer_refs=[hex(r) for r in image.pointer_refs(begin)],
                                    instruction_count=len(insns))
        for row in refs:
            if row["function_rva"] == hex(begin):
                addr = int(row["instruction_rva"], 16)
                # REX/mandatory prefix can immediately precede the searched opcode.
                actual = next((a for a in (addr, addr-1, addr-2) if a in insns
                               and f"0x{image.base+int(row['data_rva'], 16):x}" in insns[a]), None)
                row["disassembly_confirmed"] = actual is not None
                if actual is not None:
                    row["instruction_rva"] = hex(actual)
                    row["instruction"] = insns[actual]
    legacy = {}
    for name, rva in LEGACY.items():
        section = image.section(rva)
        fn = image.function(rva)
        legacy[name] = dict(rva=hex(rva), section=section["name"] if section else None,
                            function_begin=hex(fn[0]) if fn else None,
                            is_function_begin=bool(fn and fn[0] == rva))
    return dict(sha256=hashlib.sha256(image.data).hexdigest(), size=len(image.data),
                image_base=hex(image.base), runtime_function_count=len(image.functions),
                anchors={k: [hex(r) for r in v] for k, v in anchors.items()},
                references=refs, functions=functions, legacy_address_mapping=legacy), listings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--objdump", default=shutil.which("objdump"))
    parser.add_argument("--function", type=lambda s: int(s, 0), action="append", default=[])
    args = parser.parse_args()
    if not args.objdump:
        parser.error("objdump is required for instruction confirmation")
    report, listings = analyze(Image(args.module), args.objdump, args.function)
    report.update(schema="windows-hook-send-static.v1", live_process_accessed=False,
                  code_invoked=False, abi_verified=False, delivery_verified=False,
                  limitations=["Current-function semantics need further callgraph and object-layout evidence.",
                               "No candidate in this report is approved for invocation."])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    listing_dir = args.output.with_suffix("")
    listing_dir.mkdir(exist_ok=True)
    for name, listing in listings.items():
        (listing_dir / f"{name}.asm").write_text(listing, encoding="utf-8")
    print(json.dumps({"output": str(args.output), "functions": len(report["functions"]),
                      "confirmed_references": sum(bool(r.get("disassembly_confirmed")) for r in report["references"]),
                      "live_process_accessed": False}))


if __name__ == "__main__":
    main()
