# -*- coding: utf-8 -*-
# gen_cs_adder.py — 生成 2N 位 Kogge-Stone 并行前缀加法器（命名 CS_Adder{2N}）
#
# 特性：
# - 输出 Verilog 不使用 generate / for / always
# - Kogge-Stone 前缀网络在 Python 里展开为显式 assign
#
# 用法：
#   python gen_cs_adder.py --n 32 --out CS_Adder64.v
#   （模块名默认 CS_Adder{2N}；也可用 --module 覆盖）

import argparse

def gen_adder_module(N: int, module_name: str | None = None) -> str:
    assert N >= 2
    W = 2 * N
    if module_name is None:
        module_name = f"CS_Adder{W}"

    # Kogge-Stone stage count = ceil(log2(W))
    stages = (W - 1).bit_length()

    s: list[str] = []
    s.append("// -----------------------------------------------------------------------------")
    s.append(f"// Auto-generated {module_name}: Kogge-Stone parallel prefix adder (no generate/for)")
    s.append(f"// Width = {W}")
    s.append("// - Pure combinational")
    s.append("// - Carries computed via prefix G/P; cin injected at the end: c[i+1]=G[i]|(P[i]&cin)")
    s.append("// -----------------------------------------------------------------------------")
    s.append("")
    s.append(f"module {module_name} (")
    s.append(f"  input  wire [{W-1}:0] a,")
    s.append(f"  input  wire [{W-1}:0] b,")
    s.append( "  input  wire           cin,")
    s.append(f"  output wire [{W-1}:0] sum,")
    s.append( "  output wire          cout")
    s.append(");")
    s.append("")

    # bitwise propagate/generate
    s.append(f"  wire [{W-1}:0] p;")
    s.append(f"  wire [{W-1}:0] g;")
    s.append( "  assign p = a ^ b;")
    s.append( "  assign g = a & b;")
    s.append("")

    # stage 0
    s.append(f"  wire [{W-1}:0] G0;")
    s.append(f"  wire [{W-1}:0] P0;")
    s.append( "  assign G0 = g;")
    s.append( "  assign P0 = p;")
    s.append("")

    prevG = "G0"
    prevP = "P0"
    for st in range(1, stages + 1):
        dist = 1 << (st - 1)
        curG = f"G{st}"
        curP = f"P{st}"
        s.append(f"  // ---- Kogge-Stone stage {st}: distance = {dist} ----")
        s.append(f"  wire [{W-1}:0] {curG};")
        s.append(f"  wire [{W-1}:0] {curP};")
        for i in range(W):
            if i < dist:
                s.append(f"  assign {curG}[{i}] = {prevG}[{i}];")
                s.append(f"  assign {curP}[{i}] = {prevP}[{i}];")
            else:
                k = i - dist
                s.append(f"  assign {curG}[{i}] = {prevG}[{i}] | ({prevP}[{i}] & {prevG}[{k}]);")
                s.append(f"  assign {curP}[{i}] = {prevP}[{i}] & {prevP}[{k}];")
        s.append("")
        prevG, prevP = curG, curP

    Gf = prevG
    Pf = prevP

    # carry
    s.append("  // ---- Carry computation ----")
    s.append(f"  wire [{W}:0] c;")
    s.append( "  assign c[0] = cin;")
    for i in range(1, W + 1):
        s.append(f"  assign c[{i}] = {Gf}[{i-1}] | ({Pf}[{i-1}] & cin);")
    s.append("")

    # sum & cout
    s.append("  // ---- Sum & Cout ----")
    s.append(f"  assign sum  = p ^ c[{W-1}:0];")
    s.append(f"  assign cout = c[{W}];")
    s.append("")
    s.append("endmodule")
    s.append("")
    return "\n".join(s)

def main():
    ap = argparse.ArgumentParser(description="Generate CS_Adder(2N) as Kogge-Stone (no generate/for in Verilog).")
    ap.add_argument("--n", type=int, required=True, help="位宽 N（结果为 2N 位）")
    ap.add_argument("--module", type=str, default=None, help="模块名（默认 CS_Adder{2N}）")
    ap.add_argument("--out", type=str, default=None, help="输出 .v 文件（省略则打印到 stdout）")
    args = ap.parse_args()

    text = gen_adder_module(args.n, args.module)
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    else:
        print(text)

if __name__ == "__main__":
    main()
