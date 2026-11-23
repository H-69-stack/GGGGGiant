"""巨管软件 - 许可证签发工具（开发 / 维护侧 GUI）。

功能：
  - 新签发：输入客户名称、机器码、勾选账户类型、设置有效期 → 生成 license.dat
  - 续期：载入旧许可证自动回填，仅改到期日期后重新签发
  - 自检：对生成的许可证验签，确认无误

依赖：keys/private_key.pem（绝密，绝不随客户端分发）。
请用 PyInstaller / Nuitka 打包成独立 exe，仅供维护团队使用。
"""
import datetime
import os
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.abspath(os.path.join(_THIS_DIR, '..', '..', 'backend'))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

import signer  # noqa: E402
from core.common.licensing.crypto import deserialize_license, VALID_WORKROLES  # noqa: E402

ROLES = ['ALL', '工艺规划', '模具设计', '硬模成型']


class LicenseTool(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('巨管软件 · 许可证签发工具（内部专用）')
        self.geometry('640x640')
        self.resizable(False, False)
        self.password = None
        self._build()
        self._check_key()

    def _build(self):
        pad = {'padx': 10, 'pady': 6}
        frm = ttk.Frame(self)
        frm.pack(fill='both', expand=True, padx=16, pady=12)

        title = ttk.Label(frm, text='许可证签发 / 续期', font=('', 16, 'bold'))
        title.grid(row=0, column=0, columnspan=3, sticky='w', pady=(0, 10))

        ttk.Label(frm, text='客户名称').grid(row=1, column=0, sticky='w', **pad)
        self.customer_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.customer_var, width=42).grid(
            row=1, column=1, columnspan=2, sticky='w', **pad)

        ttk.Label(frm, text='机器码').grid(row=2, column=0, sticky='w', **pad)
        self.machine_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.machine_var, width=42).grid(
            row=2, column=1, columnspan=2, sticky='w', **pad)
        ttk.Label(frm, text='（客户在登录页底部或机器码工具中可看到）',
                  foreground='#888').grid(row=3, column=1, columnspan=2, sticky='w', padx=10)

        ttk.Label(frm, text='允许的账户类型').grid(row=4, column=0, sticky='nw', **pad)
        roles_frame = ttk.Frame(frm)
        roles_frame.grid(row=4, column=1, columnspan=2, sticky='w', **pad)
        self.role_vars = {}
        for i, r in enumerate(ROLES):
            v = tk.BooleanVar(value=False)
            self.role_vars[r] = v
            cb = ttk.Checkbutton(roles_frame, text=r, variable=v,
                                 command=self._on_role_toggle)
            cb.grid(row=0, column=i, sticky='w', padx=6)
        ttk.Label(frm, text='（勾选 ALL 等于全功能，会忽略其它勾选）',
                  foreground='#888').grid(row=5, column=1, columnspan=2, sticky='w', padx=10)

        ttk.Label(frm, text='签发日期').grid(row=6, column=0, sticky='w', **pad)
        self.issued_var = tk.StringVar(value=datetime.date.today().strftime('%Y-%m-%d'))
        ttk.Entry(frm, textvariable=self.issued_var, width=20).grid(
            row=6, column=1, sticky='w', **pad)

        ttk.Label(frm, text='到期日期').grid(row=7, column=0, sticky='w', **pad)
        self.expires_var = tk.StringVar(
            value=(datetime.date.today() + datetime.timedelta(days=365)).strftime('%Y-%m-%d'))
        ttk.Entry(frm, textvariable=self.expires_var, width=20).grid(
            row=7, column=1, sticky='w', **pad)
        ttk.Label(frm, text='格式 YYYY-MM-DD', foreground='#888').grid(
            row=7, column=2, sticky='w')

        # 快捷续期按钮
        quick = ttk.Frame(frm)
        quick.grid(row=8, column=1, columnspan=2, sticky='w', padx=10)
        for label, days in [('+3 个月', 90), ('+6 个月', 180), ('+1 年', 365), ('+2 年', 730)]:
            ttk.Button(quick, text=label, width=8,
                       command=lambda d=days: self._set_expiry(d)).pack(side='left', padx=3)

        ttk.Label(frm, text='最大账户数').grid(row=9, column=0, sticky='w', **pad)
        self.maxusers_var = tk.StringVar(value='0')
        ttk.Entry(frm, textvariable=self.maxusers_var, width=10).grid(
            row=9, column=1, sticky='w', **pad)
        ttk.Label(frm, text='0 = 不限', foreground='#888').grid(row=9, column=2, sticky='w')

        ttk.Label(frm, text='备注').grid(row=10, column=0, sticky='w', **pad)
        self.note_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.note_var, width=42).grid(
            row=10, column=1, columnspan=2, sticky='w', **pad)

        btns = ttk.Frame(frm)
        btns.grid(row=11, column=0, columnspan=3, pady=16)
        ttk.Button(btns, text='载入旧许可证（续期）', command=self._load_existing).pack(side='left', padx=6)
        ttk.Button(btns, text='生成许可证', command=self._generate).pack(side='left', padx=6)
        ttk.Button(btns, text='验签自检', command=self._verify).pack(side='left', padx=6)

        ttk.Label(frm, text='输出日志：').grid(row=12, column=0, sticky='nw', padx=10)
        self.log = tk.Text(frm, height=9, width=72, state='disabled', bg='#f7f7f7')
        self.log.grid(row=13, column=0, columnspan=3, padx=10, pady=(0, 10))

        self.last_output = None

    # ─── 行为 ─────────────────────────────────────────────────────────────

    def _check_key(self):
        if not os.path.exists(signer.DEFAULT_PRIVATE_KEY):
            messagebox.showwarning(
                '缺少私钥',
                f'未找到私钥：\n{signer.DEFAULT_PRIVATE_KEY}\n\n'
                '请先运行 generate_keys.py 生成密钥对，或把维护团队的私钥放到 keys/ 目录。')
        else:
            self._log(f'已加载私钥：{signer.DEFAULT_PRIVATE_KEY}')

    def _on_role_toggle(self):
        if self.role_vars['ALL'].get():
            for r in ROLES:
                if r != 'ALL':
                    self.role_vars[r].set(False)

    def _set_expiry(self, days):
        self.expires_var.set(
            (datetime.date.today() + datetime.timedelta(days=days)).strftime('%Y-%m-%d'))

    def _selected_roles(self):
        if self.role_vars['ALL'].get():
            return ['ALL']
        return [r for r in ROLES if r != 'ALL' and self.role_vars[r].get()]

    def _log(self, msg):
        self.log.configure(state='normal')
        self.log.insert('end', msg + '\n')
        self.log.see('end')
        self.log.configure(state='disabled')

    def _load_existing(self):
        path = filedialog.askopenfilename(
            title='选择要续期的旧许可证',
            filetypes=[('许可证文件', '*.dat'), ('所有文件', '*.*')])
        if not path:
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                payload, _ = deserialize_license(f.read())
            self.customer_var.set(payload.get('customer', ''))
            self.machine_var.set(payload.get('machine_fingerprint', ''))
            self.maxusers_var.set(str(payload.get('max_users', 0)))
            self.note_var.set(payload.get('note', ''))
            for r in ROLES:
                self.role_vars[r].set(r in payload.get('allowed_workroles', []))
            self._log(f'已载入旧许可证：{os.path.basename(path)}（请设置新的到期日期后重新生成）')
        except Exception as e:
            messagebox.showerror('载入失败', str(e))

    def _generate(self):
        try:
            roles = self._selected_roles()
            payload = signer.build_payload(
                customer=self.customer_var.get().strip(),
                machine_fingerprint=self.machine_var.get().replace('-', '').strip().lower(),
                allowed_workroles=roles,
                expires_at=self.expires_var.get().strip(),
                issued_at=self.issued_var.get().strip(),
                max_users=int(self.maxusers_var.get() or 0),
                note=self.note_var.get().strip(),
            )
        except Exception as e:
            messagebox.showerror('参数错误', str(e))
            return

        default_name = f'{payload.customer or "license"}_license.dat'
        out = filedialog.asksaveasfilename(
            title='保存许可证', defaultextension='.dat',
            initialfile=default_name,
            initialdir=signer.DEFAULT_ISSUED_DIR,
            filetypes=[('许可证文件', '*.dat')])
        if not out:
            return
        try:
            signer.issue_license_file(payload, out, password=self.password)
            activation_code = signer.issue_activation_code(payload, password=self.password)
            self.last_output = out
            self._log('─' * 50)
            self._log('✔ 短激活码（发给客户粘贴激活）：')
            self._log(activation_code)
            self._log('')
            self._log('✔ 备份 license.dat：' + out)
            self._log(f'  编号：{payload.license_id}')
            self._log(f'  客户：{payload.customer}')
            self._log(f'  账户类型：{"、".join(payload.allowed_workroles)}')
            self._log(f'  到期：{payload.expires_at}')
            self._log('  客户在本机激活页粘贴上述授权码即可，无需传输 license.dat。')
            self.clipboard_clear()
            self.clipboard_append(activation_code)
            messagebox.showinfo(
                '成功',
                f'激活码已复制到剪贴板：\n\n{activation_code}\n\n'
                f'license.dat 已保存至：\n{out}',
            )
        except Exception as e:
            messagebox.showerror('生成失败', str(e))

    def _verify(self):
        path = self.last_output or filedialog.askopenfilename(
            title='选择要验签的许可证', filetypes=[('许可证文件', '*.dat'), ('所有文件', '*.*')])
        if not path:
            return
        try:
            ok, payload = signer.verify_license_file(path)
            self._log(f'验签 {os.path.basename(path)}：{"通过 ✔" if ok else "失败 ✘"}')
            if ok:
                messagebox.showinfo('验签通过', '许可证签名有效。')
            else:
                messagebox.showerror('验签失败', '签名无效，请检查私钥/公钥是否配对。')
        except Exception as e:
            messagebox.showerror('验签出错', str(e))


if __name__ == '__main__':
    LicenseTool().mainloop()
