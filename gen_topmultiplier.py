
import argparse

def wrap_ident_list(prefix: str, idents, per_line=8, indent="  "):
    lines = []
    cur = []
    for i, name in enumerate(idents, 1):
        cur.append(name)
        if i % per_line == 0:
            lines.append(f"{indent}{prefix} {', '.join(cur)};")
            cur = []
    if cur:
        lines.append(f"{indent}{prefix} {', '.join(cur)};")
    return "\n".join(lines)

def gen_csa_module(width: int) -> str:
    module_name = f"CSA_{width}"
    return f"""module {module_name} (
  input  [{width-1}:0] a, b, c,
  output [{width-1}:0] sum_out,
  output [{width-1}:0] carry_out
);
  assign sum_out   = a ^ b ^ c;
  assign carry_out = (a & b) | (a & c) | (b & c);
endmodule
"""

def gen_top(N: int, top_module: str, booth_module: str, wallace_module: str, adder_module: str) -> str:
    W = 2 * N
    pp_names = [f"pp{i}" for i in range(N)]

    s = []
    s.append("// -----------------------------------------------------------------------------")
    s.append(f"// Auto-generated TopMultiplier (Stable N={N})")
    s.append("// -----------------------------------------------------------------------------")
    s.append(gen_csa_module(W))
    s.append("")

    s.append(f"module {top_module} (")
    s.append(f"  input  [{N-1}:0] x_in,")
    s.append(f"  input  [{N-1}:0] y_in,")
    s.append( "  input            signed_op,")
    s.append(f"  output [{W-1}:0] result_out")
    s.append(");")
    
    s.append(f"  wire [{N-1}:0] sign;")
    s.append("")
    s.append("  // Partial Products")
    s.append(wrap_ident_list(prefix=f"wire [{N-1}:0]", idents=pp_names, per_line=8, indent="  "))
    s.append("")
    s.append(f"  wire [{W-1}:0] wallace_sum, wallace_carry;")
    s.append("")

    # 1. Booth
    s.append(f"  {booth_module} booth (")
    s.append( "    .x_in(x_in), .y_in(y_in), .signed_op(signed_op),")
    for i in range(0, N, 4):
        chunk = [f".pp{j}({pp_names[j]})" for j in range(i, min(i+4, N))]
        s.append("    " + ", ".join(chunk) + ",")
    s.append("    .S(sign)")
    s.append("  );")
    s.append("")

    # 2. Wallace
    s.append(f"  {wallace_module} wallace (")
    for i in range(0, N, 4):
        chunk = [f".pp{j}({pp_names[j]})" for j in range(i, min(i+4, N))]
        s.append("    " + ", ".join(chunk) + ",")
    s.append("    .opa(wallace_sum), .opb(wallace_carry)")
    s.append("  );")
    s.append("")

    # 3. 符号补偿 (Parallel)
    # 这里的逻辑是通用的：不管 N 是多少，补偿都是 (~Sign) + 1 左移 N 位
    s.append(f"  wire [{N-1}:0] sign_inv = ~sign;")
    s.append(f"  wire [{N-1}:0] sign_inc = sign_inv + {N}'b1;")
    
    s.append(f"  wire [{W-1}:0] comp_term;")
    s.append(f"  assign comp_term = signed_op ? {{sign_inc, {N}'b0}} : {W}'b0;")
    s.append("")

    # 4. CSA
    s.append(f"  wire [{W-1}:0] csa_sum, csa_carry_raw;")
    s.append(f"  CSA_{W} csa_stage (")
    s.append("    .a(wallace_sum), .b(wallace_carry), .c(comp_term),")
    s.append("    .sum_out(csa_sum), .carry_out(csa_carry_raw)")
    s.append("  );")

    # 5. Final Adder
    s.append(f"  wire [{W-1}:0] final_a = csa_sum;")
    s.append(f"  wire [{W-1}:0] final_b = {{csa_carry_raw[{W-2}:0], 1'b0}};")
    
    s.append(f"  {adder_module} final_adder (")
    s.append("    .a(final_a), .b(final_b), .cin(1'b0),")
    s.append("    .sum(result_out), .cout()")
    s.append("  );")

    s.append("endmodule")
    return "\n".join(s)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, required=True)
    ap.add_argument("--top", type=str, default="TopMultiplier")
    ap.add_argument("--booth_module", type=str, default=None)
    ap.add_argument("--wallace_module", type=str, default=None)
    ap.add_argument("--adder_module", type=str, default=None)
    ap.add_argument("--out", type=str, default="TopMultiplier.v")
    args = ap.parse_args()
    
    booth = args.booth_module or f"Booth{args.n}"
    wallace = args.wallace_module or f"WallaceTree{args.n}x{args.n}"
    adder = args.adder_module or f"CS_Adder{2*args.n}"
    
    text = gen_top(args.n, args.top, booth, wallace, adder)
    if args.out:
        with open(args.out, "w") as f: f.write(text)
    else:
        print(text)

if __name__ == "__main__":
    main()