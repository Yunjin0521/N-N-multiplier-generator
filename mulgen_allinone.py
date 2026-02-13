# -*- coding: utf-8 -*-
# mulgen_allinone.py — 单文件 EXE：一键生成器（Tk） + Wallace 规划器（Qt）二合一
# 正常运行：打开 Tk 一键生成器
# 参数 --wallace：进入 Wallace 规划器模式（PySide6），支持 --n 指定位宽

import os, sys, json, traceback, subprocess, argparse

APP_TITLE = "MulGen 一键生成器（单文件版）"
DEFAULT_N = 32

# ----------------- 公用 I/O -----------------
def read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def write_text(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)

def resource_dir():
    # 打包后：EXE 同目录；开发期：当前工作目录
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.getcwd()

def load_plan_get_N(plan_path):
    try:
        obj = read_json(plan_path)
        N = int(obj.get("N"))
        return N
    except Exception:
        return None

# ----------------- Wallace 模式（Qt） -----------------
def run_wallace_planner(n):
    from PySide6.QtWidgets import QApplication
    import wallace_bubble_gui as wbg
    app = QApplication(sys.argv)
    win = wbg.MainWin(N=int(n) if n else 32)
    win.show()
    sys.exit(app.exec())

def spawn_wallace_planner(n):
    exe_path = sys.executable if getattr(sys, "frozen", False) else sys.argv[0]
    args = [exe_path, "--wallace", "--n", str(n)]
    try:
        creationflags = 0x00000200 | 0x00000008 if os.name == "nt" else 0
        subprocess.Popen(args, cwd=os.path.dirname(exe_path) or None, creationflags=creationflags)
    except Exception as e:
        raise RuntimeError(f"无法启动 Wallace 规划器：{e}")

# ----------------- Tk 一键生成器 -----------------
def run_tk_main():
    import gen_booth
    import gen_cs_adder
    import gen_wallace
    import gen_topmultiplier

    # ✅ 新增：一键生成 HalfAdder / FullAdder
    import gen_adders

    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    root = tk.Tk()
    root.title(APP_TITLE)
    root.geometry("580x360")

    frm = ttk.Frame(root, padding=12)
    frm.pack(fill="both", expand=True)

    status_text = tk.StringVar(value="请选择位宽 N，可选 plan.json，然后生成。")

    # N
    row = 0
    ttk.Label(frm, text="位宽 N：").grid(row=row, column=0, sticky="e", padx=4, pady=6)
    ent_N = ttk.Entry(frm, width=10)
    ent_N.insert(0, str(DEFAULT_N))
    ent_N.grid(row=row, column=1, sticky="w", padx=4, pady=6)

    # plan.json
    row += 1
    ttk.Label(frm, text="Wallace 方案 (plan.json)：").grid(row=row, column=0, sticky="e", padx=4, pady=6)
    plan_var = tk.StringVar(value="")
    ent_plan = ttk.Entry(frm, textvariable=plan_var, width=50)
    ent_plan.grid(row=row, column=1, sticky="w", padx=4, pady=6, columnspan=2)

    def browse_plan():
        path = filedialog.askopenfilename(title="选择 plan.json", filetypes=[("JSON", "*.json"), ("All Files", "*.*")])
        if path:
            plan_var.set(path)
            N_in_plan = load_plan_get_N(path)
            if N_in_plan:
                ent_N.delete(0, tk.END); ent_N.insert(0, str(N_in_plan))
    ttk.Button(frm, text="浏览...", command=browse_plan).grid(row=row, column=3, sticky="w", padx=4, pady=6)

    # 输出目录
    row += 1
    ttk.Label(frm, text="输出目录：").grid(row=row, column=0, sticky="e", padx=4, pady=6)
    out_var = tk.StringVar(value=resource_dir())
    ent_out = ttk.Entry(frm, textvariable=out_var, width=50)
    ent_out.grid(row=row, column=1, sticky="w", padx=4, pady=6, columnspan=2)

    def browse_out():
        path = filedialog.askdirectory(title="选择输出目录")
        if path:
            out_var.set(path)
    ttk.Button(frm, text="浏览...", command=browse_out).grid(row=row, column=3, sticky="w", padx=4, pady=6)

    # 生成逻辑
    def do_generate():
        try:
            n = int(ent_N.get())
            if n < 2:
                messagebox.showerror("非法位宽", "N 必须 ≥ 2")
                return
        except Exception:
            messagebox.showerror("非法位宽", "请输入正确的 N（整数）")
            return

        out_dir = out_var.get().strip() or resource_dir()
        plan_path = plan_var.get().strip()

        # 若提供了 plan.json，则严检 N 一致性
        if plan_path:
            if not os.path.exists(plan_path):
                messagebox.showerror("plan.json 不存在", f"找不到文件：\n{plan_path}")
                return
            N_in_plan = load_plan_get_N(plan_path)
            if N_in_plan is None:
                messagebox.showerror("plan.json 无效", "无法读取 plan.json 或缺少字段 N。")
                return
            if N_in_plan != n:
                messagebox.showerror(
                    "位宽不匹配",
                    f"plan.json 的 N = {N_in_plan} 与当前选择的 N = {n} 不一致。\n"
                    f"请调整界面的 N 或选择匹配位宽的 plan.json。"
                )
                return

        booth_mod   = f"Booth{n}"
        wallace_mod = f"WallaceTree{n}x{n}"
        adder_mod   = f"CS_Adder{2*n}"
        top_mod     = "TopMultiplier"

        try:
            # ✅ 0) Adders（HalfAdder/FullAdder）
            # 你 gen_wallace 里实例化的名字就是 HalfAdder / FullAdder，所以不要加 prefix
            adders_text = gen_adders.verilog_text(prefix="", use_sv=False)
            write_text(os.path.join(out_dir, "adders.v"), adders_text)

            # 1) Booth
            booth_text = gen_booth.gen_booth_module(n, booth_mod)
            write_text(os.path.join(out_dir, f"{booth_mod}.v"), booth_text)

            # 2) Adder
            adder_text = gen_cs_adder.gen_adder_module(n, adder_mod)
            write_text(os.path.join(out_dir, f"{adder_mod}.v"), adder_text)

            # 3) Wallace（需要 plan.json；已在前面校验 N 一致）
            if plan_path:
                plan = gen_wallace.load_plan(plan_path)
                if hasattr(plan, "N") and int(plan.N) != n:
                    messagebox.showerror(
                        "位宽不匹配",
                        f"plan.json 的 N = {getattr(plan, 'N', '未知')} 与当前选择的 N = {n} 不一致。"
                    )
                    return
                plan.module_name = wallace_mod
                try:
                    wall_text = gen_wallace.generate_verilog(plan)
                except AssertionError as e:
                    messagebox.showerror("Wallace 生成失败", f"{e}\n\n请打开 Wallace 规划器检查圈划是否压到两行。")
                    return
                write_text(os.path.join(out_dir, f"{wallace_mod}.v"), wall_text)
            else:
                messagebox.showwarning(
                    "未提供 plan.json",
                    "未选择 Wallace 方案 plan.json，已跳过 WallaceTree 生成。\n"
                    "你可以点击“打开 Wallace 规划器”进行圈划后再生成。"
                )

            # 4) Top
            top_text = gen_topmultiplier.gen_top(
                n,
                top_module=top_mod,
                booth_module=booth_mod,
                wallace_module=wallace_mod,
                adder_module=adder_mod
            )
            write_text(os.path.join(out_dir, f"{top_mod}.v"), top_text)

            status_text.set(f"生成完成：Adders / Booth / Adder / Top 已输出到 {out_dir}")
            messagebox.showinfo("完成", f"生成完成！\n输出目录：{out_dir}\n\n已额外生成：adders.v（HalfAdder/FullAdder）")

        except Exception as e:
            traceback.print_exc()
            messagebox.showerror("生成失败", f"{e}")

    # 按钮行
    row += 1
    btn_gen = ttk.Button(frm, text="一键生成", width=18, command=do_generate)
    btn_gui = ttk.Button(frm, text="打开 Wallace 规划器", width=20,
                         command=lambda: spawn_wallace_planner(ent_N.get()))
    btn_exit = ttk.Button(frm, text="退出", width=8, command=root.destroy)
    btn_gen.grid(row=row, column=1, sticky="w", padx=4, pady=14)
    btn_gui.grid(row=row, column=2, sticky="w", padx=4, pady=14)
    btn_exit.grid(row=row, column=3, sticky="e", padx=4, pady=14)

    # 状态栏
    row += 1
    ttk.Label(frm, textvariable=status_text, foreground="#555").grid(row=row, column=0, columnspan=4, sticky="w", padx=4, pady=6)

    root.mainloop()

# ----------------- 入口 -----------------
def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--wallace", action="store_true")
    parser.add_argument("--n", type=int, default=None)
    try:
        args, _ = parser.parse_known_args()
    except SystemExit:
        args = argparse.Namespace(wallace=False, n=None)

    if args.wallace:
        run_wallace_planner(args.n or DEFAULT_N)
    else:
        run_tk_main()

if __name__ == "__main__":
    main()
