from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
import json
import argparse


@dataclass
class Adder:
    type: str                 # "HA" | "FA"
    col: int                  # 权重列
    inputs: Optional[List[str]] = None  # 指定输入（否则自动从该列顶部取 2/3 个）


@dataclass
class Stage:
    name: str
    ha_name_fmt: str = "{stage}ha{idx}"
    fa_name_fmt: str = "{stage}fa{idx}"
    sum_bus: str = "{stage}_S"
    carry_bus: str = "{stage}_C"
    adders: List[Adder] = field(default_factory=list)
    color: Optional[str] = None
    baseline: Optional[Dict[int, List[float]]] = None
    positions: Optional[Dict[str, float]] = None


@dataclass
class Plan:
    N: int
    stages: List[Stage] = field(default_factory=list)
    module_name: str = "WallaceTreeNxN"


# -------------------- 初始列：pp bits 按权重列填充 --------------------
def build_initial_columns(N: int) -> List[List[str]]:
    cols = [[] for _ in range(2 * N - 1)]
    for i in range(N):
        for j in range(N):
            cols[i + j].append(f"pp{i}[{j}]")
    return cols


# 仅替换 {stage}，保留 {idx}
def _fmt_bus(name_tmpl: str, stage: str) -> str:
    return name_tmpl.replace("{stage}", stage)


def _remove_token_once(columns: List[List[str]], preferred_col: int, token: str) -> bool:
    """
    尝试从 columns 中移除 token（只移一次）：
    1) 优先从 preferred_col 列移除
    2) 若找不到，再遍历所有列移除一次
    返回是否成功移除
    """
    if token in ("1'b0", "1'b1"):
        return True  # 常量不用移除

    # 1) preferred col
    if 0 <= preferred_col < len(columns):
        try:
            columns[preferred_col].remove(token)
            return True
        except ValueError:
            pass

    # 2) global search
    for pile in columns:
        try:
            pile.remove(token)
            return True
        except ValueError:
            continue
    return False


def apply_stage(columns: List[List[str]], st: Stage) -> Tuple[Tuple[str, int], Tuple[str, int], List[str], List[str]]:
    sum_bus = _fmt_bus(st.sum_bus, st.name)
    car_bus = _fmt_bus(st.carry_bus, st.name)

    inst_lines: List[str] = []
    warn_lines: List[str] = []

    ha_idx = 0
    fa_idx = 0

    s_seq = 0
    c_seq = 0

    for ad in st.adders:
        typ = (ad.type or "").upper()
        col = int(ad.col)

        # 取输入
        if ad.inputs and len(ad.inputs) > 0:
            inps = list(ad.inputs)
            for token in inps:
                ok = _remove_token_once(columns, col, token)
                if (not ok) and token not in ("1'b0", "1'b1"):
                    warn_lines.append(
                        f"// WARNING: Stage {st.name} col {col} 指定输入未在列堆中找到：{token}（可能是编号不一致或重复使用）"
                    )
        else:
            # 没给 inputs：从该列“堆”里取
            need = 3 if typ == "FA" else 2
            if col < 0 or col >= len(columns):
                warn_lines.append(f"// WARNING: Stage {st.name} col {col} 越界，无可用位，补 1'b0")
                inps = ["1'b0"] * need
            else:
                pile = columns[col]
                take: List[str] = []
                for _ in range(need):
                    take.append(pile.pop() if pile else "1'b0")
                inps = take

        
        s_bit = f"{sum_bus}[{s_seq}]"
        c_bit = f"{car_bus}[{c_seq}]"
        s_seq += 1
        c_seq += 1

        if typ == "FA":
            inst = _fmt_bus(st.fa_name_fmt, st.name).replace("{idx}", str(fa_idx))
            fa_idx += 1
            a, b, cin = (inps + ["1'b0", "1'b0", "1'b0"])[:3]
            inst_lines.append(f"  FullAdder {inst} ( {a}, {b}, {cin}, {s_bit}, {c_bit} );")
        elif typ == "HA":
            inst = _fmt_bus(st.ha_name_fmt, st.name).replace("{idx}", str(ha_idx))
            ha_idx += 1
            a, b = (inps + ["1'b0", "1'b0"])[:2]
            inst_lines.append(f"  HalfAdder {inst} ( {a}, {b}, {s_bit}, {c_bit} );")
        else:
            warn_lines.append(f"// WARNING: Unknown adder type '{ad.type}' at stage {st.name}, col {col} (skipped)")
            continue

        # 写回列：S -> col
        if col >= 0:
            while col >= len(columns):
                columns.append([])
            columns[col].append(s_bit)
        else:
            warn_lines.append(f"// WARNING: S 写回列 {col} < 0 被丢弃：{s_bit}")

        # 写回列：C -> col+1
        colp1 = col + 1
        if colp1 >= 0:
            while colp1 >= len(columns):
                columns.append([])
            columns[colp1].append(c_bit)
        else:
            warn_lines.append(f"// WARNING: C 写回列 {colp1} < 0 被丢弃：{c_bit}")

    
    sum_w = s_seq
    car_w = c_seq
    return (sum_bus, sum_w), (car_bus, car_w), inst_lines, warn_lines


def generate_verilog(plan: Plan) -> str:
    N = int(plan.N)
    W = 2 * N
    cols = build_initial_columns(N)

    lines: List[str] = []
    inst_lines: List[str] = []
    decl_lines: List[str] = []
    warn_lines: List[str] = []

    mname = plan.module_name or f"WallaceTree{N}x{N}"


    lines.append(f"module {mname} (")
    for i in range(N):
        lines.append(f"  input wire [{N-1}:0] pp{i},")
    lines.append(f"  output wire [{W-1}:0] opa,")
    lines.append(f"  output wire [{W-1}:0] opb")
    lines.append(");")
    lines.append("")

    for st in plan.stages:
        (sum_bus, sum_w), (car_bus, car_w), insts, warns = apply_stage(cols, st)

        if sum_w > 0:
            decl_lines.append(f"  wire [{sum_w-1}:0] {sum_bus};")
        else:
            decl_lines.append(f"  wire {sum_bus}; // empty")

        if car_w > 0:
            decl_lines.append(f"  wire [{car_w-1}:0] {car_bus};")
        else:
            decl_lines.append(f"  wire {car_bus}; // empty")

        if getattr(st, "color", None):
            decl_lines.append(f"  // stage {st.name} color {st.color}")

        inst_lines.append(f"  // ===== Stage {st.name} =====")
        inst_lines.extend(insts)
        warn_lines.extend(warns)
        inst_lines.append("")

    if decl_lines:
        lines.extend(decl_lines)
        lines.append("")
    if inst_lines:
        lines.extend(inst_lines)

    # 生成 opa/opb
    opa_bits = ["1'b0"] * W
    opb_bits = ["1'b0"] * W

    for col in range(min(len(cols), W)):
        rem = cols[col]
        if len(rem) == 0:
            continue
        elif len(rem) == 1:
            opa_bits[col] = rem[0]
        else:
            opa_bits[col] = rem[0]
            opb_bits[col] = rem[1]
            if len(rem) > 2:
                warn_lines.append(f"// WARNING: 列 {col} 剩余 {len(rem)} 位 > 2，已截断到前两位落地 opa/opb")

    lines.append(f"  assign opa = {{{', '.join(reversed(opa_bits))}}};")
    lines.append(f"  assign opb = {{{', '.join(reversed(opb_bits))}}};")
    lines.append("")

    if warn_lines:
        lines.append("  // ---------------- Warnings ----------------")
        lines.extend(warn_lines)
        lines.append("")

    lines.append("endmodule")
    lines.append("")
    return "\n".join(lines)


# ---------- JSON I/O ----------
def load_plan(json_path: str) -> Plan:
    with open(json_path, "r", encoding="utf-8") as f:
        obj = json.load(f)
    N = int(obj.get("N", 32))
    module_name = obj.get("module_name", f"WallaceTree{N}x{N}")
    stages: List[Stage] = []
    for s in obj.get("stages", []):
        st = Stage(
            name=s["name"],
            ha_name_fmt=s.get("ha_name_fmt", "{stage}ha{idx}"),
            fa_name_fmt=s.get("fa_name_fmt", "{stage}fa{idx}"),
            sum_bus=s.get("sum_bus", "{stage}_S"),
            carry_bus=s.get("carry_bus", "{stage}_C"),
            color=s.get("color"),
            baseline=s.get("baseline"),
            positions=s.get("positions"),
            adders=[]
        )
        for a in s.get("adders", []):
            st.adders.append(Adder(
                type=a["type"],
                col=int(a["col"]),
                inputs=list(a.get("inputs", [])) if a.get("inputs") else None
            ))
        stages.append(st)
    return Plan(N=N, stages=stages, module_name=module_name)


def save_plan(plan: Plan, json_path: str):
    obj = {
        "N": plan.N,
        "module_name": plan.module_name,
        "stages": []
    }
    for st in plan.stages:
        s = {
            "name": st.name,
            "ha_name_fmt": st.ha_name_fmt,
            "fa_name_fmt": st.fa_name_fmt,
            "sum_bus": st.sum_bus,
            "carry_bus": st.carry_bus,
            "adders": [],
        }
        if st.color is not None:
            s["color"] = st.color
        if st.baseline is not None:
            s["baseline"] = st.baseline
        if st.positions is not None:
            s["positions"] = st.positions
        for ad in st.adders:
            a = {"type": ad.type, "col": ad.col}
            if ad.inputs:
                a["inputs"] = ad.inputs
            s["adders"].append(a)
        obj["stages"].append(s)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


# ---------- CLI ----------
def main():
    ap = argparse.ArgumentParser(description="Generate Wallace Tree Verilog from JSON plan.")
    ap.add_argument("--plan", required=False, help="path to JSON plan")
    ap.add_argument("--out", required=False, help="output verilog file")
    ap.add_argument("--n", type=int, default=32, help="bit width N when plan not provided")
    ap.add_argument("--module", type=str, default=None, help="override module name")
    args = ap.parse_args()

    if args.plan:
        plan = load_plan(args.plan)
        if args.module:
            plan.module_name = args.module
    else:
        plan = Plan(N=args.n, stages=[], module_name=(args.module or f"WallaceTree{args.n}x{args.n}"))

    text = generate_verilog(plan)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        print(text)


if __name__ == "__main__":
    main()
