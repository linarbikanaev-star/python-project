import os
import sys
import time
from datetime import datetime
import json
import csv
import io
import threading
import tkinter as tk
import customtkinter as ctk

import requests
import paramiko
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

# Палитра дизайна Cyber Dark Glass
BG_MAIN = "#0b0c10"
SIDEBAR_BG = "#101217"
CARD_BG = "#151821"
CARD_INNER = "#1b1f2b"
BORDER_COLOR = "#232838"

TEXT_WHITE = "#f3f4f6"
TEXT_MUTED = "#828b9e"
NEON_GREEN = "#10b981"
NEON_RED = "#ef4444"
PANIC_RED = "#991b1b"
ACCENT_BLUE = "#3b82f6"

CONFIG_SERVERS_FILE = "saved_servers.json"
BOT_RISK_FILE = "risk_config.json"
TG_TOKEN = "8671375410:AAHuL2IQC6hvAEG1ZX8fCnmGb7CAP8c0BHU"
TG_CHAT_ID = "6248193776"

ctk.set_appearance_mode("dark")

WATCH_PAIRS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
PAIR_MAP = {
    "BTC/USDT": "BTCUSDT",
    "ETH/USDT": "ETHUSDT",
    "SOL/USDT": "SOLUSDT",
    "XRP/USDT": "XRPUSDT"
}
TIMEFRAME_MAP = {
    "1д": ("15", 96),
    "1н": ("60", 168),
    "1м": ("240", 180)
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


class TradingTerminalPro(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Trading Terminal Pro — Algorithmic Lab")
        self.geometry("1380x850")
        self.minsize(750, 500)
        self.configure(fg_color=BG_MAIN)

        self.is_running = True
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Состояние терминала
        self.start_time = time.time()
        self.bot_active = False
        self.server_connected = False
        self.current_server_ip = ""
        self.current_server_pwd = ""
        self.current_server_port = 22

        self.market_prices = {}
        self.server_data = {
            "status": "STOPPED",
            "cash": 1000.0,
            "initial_balance": 1000.0,
            "positions": {}
        }
        self.audit_raw = ""

        # Параметры риск-менеджмента по ТЗ
        self.risk_settings = {
            "ema_period": 50,
            "donchian_period": 10,
            "stop_loss_pct": 2.0,
            "take_profit_pct": 4.0,
            "max_daily_dd": 10.0,
            "order_size_usdt": 200.0
        }

        # Данные графиков
        self.selected_sym = "BTCUSDT"
        self.selected_period = "1д"
        self.chart_df = None
        self.chart_x_nums = None
        self.last_hover_idx = -1

        # Контейнер сетки
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # 1. Экран входа
        self.login_view = self._build_login_screen()

        # 2. Основное рабочее пространство
        self.main_app_view = ctk.CTkFrame(self, fg_color="transparent")
        self._build_main_terminal_structure()

        # Фоновые потоки обновления
        threading.Thread(target=self._market_data_worker, daemon=True).start()
        threading.Thread(target=self._uptime_timer_worker, daemon=True).start()

        self.login_view.pack(fill="both", expand=True)

    def _on_close(self):
        self.is_running = False
        plt.close('all')
        self.destroy()

    def safe_gui(self, func, *args, **kwargs):
        if self.is_running:
            try:
                self.after(0, lambda: func(*args, **kwargs))
            except Exception:
                pass

    def _attach_menu(self, entry):
        menu = tk.Menu(entry, tearoff=0, bg=CARD_BG, fg=TEXT_WHITE)
        def do_paste():
            try: entry.insert(tk.INSERT, self.clipboard_get())
            except Exception: pass
        def do_copy():
            try:
                self.clipboard_clear()
                self.clipboard_append(entry.selection_get())
            except Exception: pass
        menu.add_command(label="Вставить", command=do_paste)
        menu.add_command(label="Копировать", command=do_copy)
        entry.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))

    # ================= 1. ЭКРАН ВХОДА =================
    def _build_login_screen(self):
        frame = ctk.CTkFrame(self, fg_color=BG_MAIN)
        
        dots_frame = ctk.CTkFrame(frame, fg_color="transparent", height=24)
        dots_frame.pack(fill="x", padx=20, pady=(15, 0))
        for c in ["#ff5f56", "#ffbd2e", "#27c93f"]:
            tk.Label(dots_frame, text="●", fg=c, bg=BG_MAIN, font=("", 10)).pack(side="left", padx=2)

        card = ctk.CTkFrame(frame, width=450, corner_radius=16, fg_color=CARD_BG, border_width=1, border_color=BORDER_COLOR)
        card.place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(card, text="ВХОД В ТОРГОВЫЙ ТЕРМИНАЛ", font=ctk.CTkFont(size=18, weight="bold"), text_color=TEXT_WHITE).pack(padx=30, pady=(28, 4))
        ctk.CTkLabel(card, text="Управление институциональным ботом Bybit на VPS", font=ctk.CTkFont(size=11), text_color=TEXT_MUTED).pack(padx=30, pady=(0, 20))

        self.servers_cache = self._load_saved_servers()
        server_names = list(self.servers_cache.keys()) if self.servers_cache else ["Новый сервер"]

        ctk.CTkLabel(card, text="ВЫБЕРИТЕ СЕРВЕР ИЗ СПИСКА:", font=ctk.CTkFont(size=10, weight="bold"), text_color=TEXT_MUTED).pack(padx=35, anchor="w")
        self.dd_saved_servers = ctk.CTkOptionMenu(
            card, values=server_names, width=380, height=36, corner_radius=8,
            fg_color=CARD_INNER, button_color=BORDER_COLOR, command=self._on_saved_server_picked
        )
        self.dd_saved_servers.pack(padx=35, pady=(4, 12))

        ctk.CTkLabel(card, text="IP-АДРЕС СЕРВЕРА:", font=ctk.CTkFont(size=10, weight="bold"), text_color=TEXT_MUTED).pack(padx=35, anchor="w")
        self.entry_login_ip = ctk.CTkEntry(card, placeholder_text="185.xxx.xxx.xxx", width=380, height=38, corner_radius=8, fg_color=CARD_INNER, border_color=BORDER_COLOR)
        self.entry_login_ip.pack(padx=35, pady=(4, 10))
        self._attach_menu(self.entry_login_ip)

        ctk.CTkLabel(card, text="ПАРОЛЬ ROOT:", font=ctk.CTkFont(size=10, weight="bold"), text_color=TEXT_MUTED).pack(padx=35, anchor="w")
        self.entry_login_pwd = ctk.CTkEntry(card, placeholder_text="Пароль доступа", show="*", width=380, height=38, corner_radius=8, fg_color=CARD_INNER, border_color=BORDER_COLOR)
        self.entry_login_pwd.pack(padx=35, pady=(4, 15))
        self._attach_menu(self.entry_login_pwd)

        if self.servers_cache:
            first = list(self.servers_cache.values())[0]
            self.entry_login_ip.insert(0, first.get("ip", ""))
            self.entry_login_pwd.insert(0, first.get("pwd", ""))

        self.btn_login_connect = ctk.CTkButton(
            card, text="⚡ Подключиться к VPS", width=380, height=42, corner_radius=8,
            fg_color=ACCENT_BLUE, hover_color="#2563eb", font=ctk.CTkFont(size=13, weight="bold"),
            command=self._do_connect_login
        )
        self.btn_login_connect.pack(padx=35, pady=4)

        btn_demo = ctk.CTkButton(
            card, text="Открыть Демо-лабораторию (без сервера)", width=380, height=36, corner_radius=8,
            fg_color=CARD_INNER, hover_color=BORDER_COLOR, font=ctk.CTkFont(size=11), text_color=TEXT_MUTED,
            command=self._do_demo_mode
        )
        btn_demo.pack(padx=35, pady=(6, 12))

        self.lbl_login_msg = ctk.CTkLabel(card, text="", font=ctk.CTkFont(size=11), text_color=NEON_RED)
        self.lbl_login_msg.pack(padx=35, pady=(0, 20))
        return frame

    def _load_saved_servers(self):
        if os.path.exists(CONFIG_SERVERS_FILE):
            try:
                with open(CONFIG_SERVERS_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _on_saved_server_picked(self, val):
        info = self.servers_cache.get(val)
        if info:
            self.entry_login_ip.delete(0, "end")
            self.entry_login_ip.insert(0, info.get("ip", ""))
            self.entry_login_pwd.delete(0, "end")
            self.entry_login_pwd.insert(0, info.get("pwd", ""))

    def _do_connect_login(self):
        ip = self.entry_login_ip.get().strip()
        pwd = self.entry_login_pwd.get().strip()
        if not ip or not pwd:
            self.lbl_login_msg.configure(text="Заполните IP и пароль сервера!", text_color=NEON_RED)
            return

        self.lbl_login_msg.configure(text="Подключение по SSH...", text_color=ACCENT_BLUE)

        def worker():
            try:
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                client.connect(hostname=ip, port=22, username="root", password=pwd, timeout=5)
                client.close()

                self.current_server_ip = ip
                self.current_server_pwd = pwd
                self.server_connected = True

                srv_name = f"VPS ({ip})"
                self.servers_cache[srv_name] = {"ip": ip, "pwd": pwd, "port": 22}
                with open(CONFIG_SERVERS_FILE, "w") as f:
                    json.dump(self.servers_cache, f, indent=4)

                self.safe_gui(self._open_main_terminal)
            except Exception as e:
                self.safe_gui(self.lbl_login_msg.configure, text=f"Сбой подключения: {e}", text_color=NEON_RED)

        threading.Thread(target=worker, daemon=True).start()

    def _do_demo_mode(self):
        self.server_connected = False
        self._open_main_terminal()

    def _open_main_terminal(self):
        self.login_view.pack_forget()
        self.main_app_view.pack(fill="both", expand=True)

        if self.server_connected:
            self.hdr_server_badge.configure(text=f"● VPS Онлайн ({self.current_server_ip})", text_color=NEON_GREEN)
            self._fetch_vps_files()
        else:
            self.hdr_server_badge.configure(text="● Демо-режим (Без VPS)", text_color="#f39c12")
            self._render_active_positions()

        self.trigger_chart_refresh()

    # ================= 2. ОСНОВНАЯ СТРУКТУРА =================
    def _build_main_terminal_structure(self):
        self.main_app_view.grid_columnconfigure(1, weight=1)
        self.main_app_view.grid_rowconfigure(0, weight=1)

        # Боковое меню
        self.sidebar = ctk.CTkFrame(self.main_app_view, width=230, corner_radius=0, fg_color=SIDEBAR_BG, border_width=1, border_color=BORDER_COLOR)
        self.sidebar.grid(row=0, column=0, sticky="nsew")

        dots = ctk.CTkFrame(self.sidebar, fg_color="transparent", height=24)
        dots.pack(fill="x", padx=16, pady=(15, 12))
        for c in ["#ff5f56", "#ffbd2e", "#27c93f"]:
            tk.Label(dots, text="●", fg=c, bg=SIDEBAR_BG, font=("", 10)).pack(side="left", padx=2)

        nav_items = [
            ("📊  Рынок Live", "market"),
            ("🎛️  Дашборд бота", "dashboard"),
            ("⇄  Ручная торговля", "manual"),
            ("📉  Бэктестинг и симуляция", "backtest"),
            ("💻  Терминал и состояние сервера", "terminal"),
            ("📈  Аналитика и журнал", "analytics"),
            ("🛡️  Риск-менеджмент", "risk"),
            ("⚙️  Настройки", "settings")
        ]

        self.nav_buttons = {}
        for label, view_id in nav_items:
            btn = ctk.CTkButton(
                self.sidebar, text=label, anchor="w", height=40, corner_radius=8,
                fg_color="transparent", hover_color=CARD_BG,
                text_color=TEXT_WHITE if view_id == "dashboard" else TEXT_MUTED,
                font=ctk.CTkFont(size=12, weight="bold"),
                command=lambda v=view_id: self._navigate_view(v)
            )
            btn.pack(fill="x", padx=10, pady=2)
            self.nav_buttons[view_id] = btn

        self.nav_buttons["dashboard"].configure(fg_color=CARD_BG)

        # Рабочая зона
        self.work_area = ctk.CTkFrame(self.main_app_view, fg_color="transparent")
        self.work_area.grid(row=0, column=1, sticky="nsew", padx=14, pady=12)
        self.work_area.grid_columnconfigure(0, weight=1)
        self.work_area.grid_rowconfigure(1, weight=1)

        # Хедер
        self.header = ctk.CTkFrame(self.work_area, corner_radius=12, fg_color=CARD_BG, border_width=1, border_color=BORDER_COLOR, height=62)
        self.header.grid(row=0, column=0, sticky="ew", pady=(0, 10))

        b_box = ctk.CTkFrame(self.header, fg_color="transparent")
        b_box.pack(side="left", padx=14, pady=8)
        ctk.CTkLabel(b_box, text="Общий баланс портфеля:", font=ctk.CTkFont(size=10), text_color=TEXT_MUTED).pack(anchor="w")
        self.hdr_balance = ctk.CTkLabel(b_box, text="$1,000.00 USDT / ≈0.0131 BTC", font=ctk.CTkFont(size=13, weight="bold"), text_color=TEXT_WHITE)
        self.hdr_balance.pack(anchor="w")

        p_box = ctk.CTkFrame(self.header, fg_color="transparent")
        p_box.pack(side="left", padx=14, pady=8)
        ctk.CTkLabel(p_box, text="PnL за 24 ч:", font=ctk.CTkFont(size=10), text_color=TEXT_MUTED).pack(anchor="w")
        self.hdr_pnl = ctk.CTkLabel(p_box, text="+0.00$ (+0.00%)", font=ctk.CTkFont(size=13, weight="bold"), text_color=NEON_GREEN)
        self.hdr_pnl.pack(anchor="w")

        s_box = ctk.CTkFrame(self.header, fg_color="transparent")
        s_box.pack(side="left", padx=14, pady=8)
        ctk.CTkLabel(s_box, text="Статус подключения биржи", font=ctk.CTkFont(size=10), text_color=TEXT_MUTED).pack(anchor="w")
        self.hdr_ws = ctk.CTkLabel(s_box, text="● WS: Bybit Live (12мс)", font=ctk.CTkFont(size=12, weight="bold"), text_color=NEON_GREEN)
        self.hdr_ws.pack(anchor="w")

        u_box = ctk.CTkFrame(self.header, fg_color="transparent")
        u_box.pack(side="right", padx=12, pady=8)
        ctk.CTkLabel(u_box, text="Статус бота на сервере:", font=ctk.CTkFont(size=10), text_color=TEXT_MUTED).pack(anchor="e")
        self.hdr_server_badge = ctk.CTkLabel(u_box, text="○ БОТ НА ПАУЗЕ", font=ctk.CTkFont(size=11, weight="bold"), text_color="#f59e0b")
        self.hdr_server_badge.pack(anchor="e")

        self.btn_panic = ctk.CTkButton(
            self.header, text="Экстренно закрыть\nвсе ордера и стоп",
            corner_radius=8, fg_color=PANIC_RED, hover_color="#7f1d1d",
            font=ctk.CTkFont(size=10, weight="bold"), height=44,
            command=self._panic_emergency_stop
        )
        self.btn_panic.pack(side="right", padx=10)

        # Контейнер для смены экранов
        self.views_container = ctk.CTkFrame(self.work_area, fg_color="transparent")
        self.views_container.grid(row=1, column=0, sticky="nsew")

        self.screen_dashboard = self._build_view_dashboard()
        self.screen_market = self._build_view_market()
        self.screen_manual = self._build_view_manual()
        self.screen_backtest = self._build_view_backtest()
        self.screen_terminal = self._build_view_terminal()
        self.screen_analytics = self._build_view_analytics()
        self.screen_risk = self._build_view_risk()
        self.screen_settings = self._build_view_settings()

        self.screen_dashboard.pack(fill="both", expand=True)

    def _navigate_view(self, vid):
        for k, btn in self.nav_buttons.items():
            if k == vid:
                btn.configure(fg_color=CARD_BG, text_color=TEXT_WHITE)
            else:
                btn.configure(fg_color="transparent", text_color=TEXT_MUTED)

        for s in [self.screen_dashboard, self.screen_market, self.screen_manual,
                  self.screen_backtest, self.screen_terminal, self.screen_analytics,
                  self.screen_risk, self.screen_settings]:
            s.pack_forget()

        mapping = {
            "dashboard": self.screen_dashboard,
            "market": self.screen_market,
            "manual": self.screen_manual,
            "backtest": self.screen_backtest,
            "terminal": self.screen_terminal,
            "analytics": self.screen_analytics,
            "risk": self.screen_risk,
            "settings": self.screen_settings
        }
        mapping[vid].pack(fill="both", expand=True)

        if vid == "analytics":
            self._calculate_and_render_analytics()
        elif vid == "market":
            self.trigger_chart_refresh()

    # ================= 3. ДАШБОРД БОТА =================
    def _build_view_dashboard(self):
        frame = ctk.CTkScrollableFrame(self.views_container, fg_color="transparent")

        top_row = ctk.CTkFrame(frame, fg_color="transparent")
        top_row.pack(fill="x", pady=(0, 8))
        top_row.grid_columnconfigure((0, 1, 2, 3), weight=1)

        self.card_free_cash = self._create_kpi_box(top_row, "💼 Свободный баланс:", "$1,000.00 USDT", 0)
        self.card_funds_trade = self._create_kpi_box(top_row, "📊 Funds in Trades", "$0.00 USDT", 1)
        self.card_unrealized_pnl = self._create_kpi_box(top_row, "📈 Total Unrealized PnL", "+$0.00 (+0.00%)", 2)
        self.card_bot_uptime = self._create_kpi_box(top_row, "⏱️ Bot Uptime", "00d 00h 00m 00s", 3)

        self.btn_bot_toggle = ctk.CTkButton(
            top_row, text="▶️ Запустить бота", corner_radius=10, fg_color="#1c3b2b",
            hover_color=NEON_GREEN, border_width=1, border_color=BORDER_COLOR,
            font=ctk.CTkFont(size=12, weight="bold"), height=52,
            command=self._toggle_bot_state
        )
        self.btn_bot_toggle.grid(row=0, column=4, padx=5, sticky="nsew")

        # Таблица активных позиций
        pos_card = ctk.CTkFrame(frame, corner_radius=12, fg_color=CARD_BG, border_width=1, border_color=BORDER_COLOR)
        pos_card.pack(fill="x", pady=6)

        pos_hdr = ctk.CTkFrame(pos_card, fg_color="transparent")
        pos_hdr.pack(fill="x", padx=16, pady=(10, 4))
        ctk.CTkLabel(pos_hdr, text="Active Positions (Максимум 2 позиции по ТЗ)", font=ctk.CTkFont(size=13, weight="bold"), text_color=TEXT_WHITE).pack(side="left")

        self.pos_rows_container = ctk.CTkFrame(pos_card, fg_color="transparent")
        self.pos_rows_container.pack(fill="x", padx=14, pady=(0, 10))

        # Split: Слева лог, справа виджеты
        split = ctk.CTkFrame(frame, fg_color="transparent")
        split.pack(fill="both", expand=True, pady=6)
        split.grid_columnconfigure(0, weight=6)
        split.grid_columnconfigure(1, weight=4)

        act_card = ctk.CTkFrame(split, corner_radius=12, fg_color=CARD_BG, border_width=1, border_color=BORDER_COLOR)
        act_card.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        act_top = ctk.CTkFrame(act_card, fg_color="transparent")
        act_top.pack(fill="x", padx=14, pady=(10, 4))
        ctk.CTkLabel(act_top, text="Recent Activity Log (trade_audit.csv)", font=ctk.CTkFont(size=13, weight="bold"), text_color=TEXT_WHITE).pack(side="left")

        self.txt_activity_log = ctk.CTkTextbox(act_card, height=230, fg_color=CARD_INNER, font=ctk.CTkFont(family="Consolas", size=11))
        self.txt_activity_log.pack(fill="both", expand=True, padx=12, pady=10)

        r_col = ctk.CTkFrame(split, fg_color="transparent")
        r_col.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        # 1. Market Overview
        w1 = ctk.CTkFrame(r_col, corner_radius=10, fg_color=CARD_BG, border_width=1, border_color=BORDER_COLOR)
        w1.pack(fill="x", pady=(0, 5))
        w1_t = ctk.CTkFrame(w1, fg_color="transparent")
        w1_t.pack(fill="x", padx=12, pady=(6, 0))
        ctk.CTkLabel(w1_t, text="~ Market Overview (BTC)", font=ctk.CTkFont(size=10, weight="bold"), text_color=TEXT_MUTED).pack(side="left")
        self.lbl_mini_btc = ctk.CTkLabel(w1_t, text="$76,000.00", font=ctk.CTkFont(size=11, weight="bold"), text_color=NEON_GREEN)
        self.lbl_mini_btc.pack(side="right")

        self.fig_spark, self.ax_spark = plt.subplots(figsize=(3.2, 0.9), dpi=80, facecolor=CARD_BG)
        self.ax_spark.set_facecolor(CARD_BG)
        self.ax_spark.axis("off")
        self.canvas_spark = FigureCanvasTkAgg(self.fig_spark, master=w1)
        self.canvas_spark.get_tk_widget().pack(fill="x", padx=8, pady=(0, 4))

        # 2. Server Terminal preview
        w2 = ctk.CTkFrame(r_col, corner_radius=10, fg_color=CARD_BG, border_width=1, border_color=BORDER_COLOR)
        w2.pack(fill="x", pady=4)
        w2_t = ctk.CTkFrame(w2, fg_color="transparent")
        w2_t.pack(fill="x", padx=10, pady=(6, 2))
        for c in ["#ff5f56", "#ffbd2e", "#27c93f"]:
            tk.Label(w2_t, text="●", fg=c, bg=CARD_BG, font=("", 8)).pack(side="left", padx=1)
        ctk.CTkLabel(w2_t, text="Server Terminal", font=ctk.CTkFont(size=10, weight="bold"), text_color=TEXT_MUTED).pack(side="left", padx=6)

        self.txt_term_preview = ctk.CTkTextbox(w2, height=85, fg_color=CARD_INNER, font=ctk.CTkFont(family="Consolas", size=9))
        self.txt_term_preview.pack(fill="x", padx=8, pady=(0, 8))
        self.txt_term_preview.insert("end", "[СИСТЕМА] Bybit WebSocket подключен\n[СИСТЕМА] Мониторинг пар: BTC, ETH, SOL, XRP\n[АЛГОРИТМ] Модель риска: 1.0% | 1h + 1d MTF\n")

        # 3. Risk Summary
        w3 = ctk.CTkFrame(r_col, corner_radius=10, fg_color=CARD_BG, border_width=1, border_color=BORDER_COLOR)
        w3.pack(fill="x", pady=(4, 0))
        w3_b = ctk.CTkFrame(w3, fg_color="transparent")
        w3_b.pack(fill="x", padx=12, pady=6)
        ctk.CTkLabel(w3_b, text="🛡️ Risk Model (ТЗ)", font=ctk.CTkFont(size=11, weight="bold"), text_color=TEXT_WHITE).pack(side="left")
        self.lbl_risk_summary_text = ctk.CTkLabel(w3_b, text="1% Risk | 2xATR | Max 2 Pos", font=ctk.CTkFont(size=10), text_color=TEXT_MUTED)
        self.lbl_risk_summary_text.pack(side="right")

        return frame

    def _create_kpi_box(self, parent, title, val, col, color=TEXT_WHITE):
        card = ctk.CTkFrame(parent, corner_radius=10, fg_color=CARD_BG, border_width=1, border_color=BORDER_COLOR)
        card.grid(row=0, column=col, padx=4, sticky="nsew")
        ctk.CTkLabel(card, text=title, font=ctk.CTkFont(size=10), text_color=TEXT_MUTED).pack(padx=10, pady=(8, 1), anchor="w")
        lbl = ctk.CTkLabel(card, text=val, font=ctk.CTkFont(size=14, weight="bold"), text_color=color)
        lbl.pack(padx=10, pady=(0, 8), anchor="w")
        return lbl

    # ================= 4. РЫНОК LIVE =================
    def _build_view_market(self):
        frame = ctk.CTkFrame(self.views_container, fg_color="transparent")

        top = ctk.CTkFrame(frame, fg_color="transparent")
        top.pack(fill="x", pady=(4, 6))

        self.seg_market_coins = ctk.CTkSegmentedButton(
            top, values=["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT"],
            command=self._on_mkt_coin_changed, corner_radius=8,
            fg_color=CARD_BG, selected_color=BORDER_COLOR
        )
        self.seg_market_coins.set("BTC/USDT")
        self.seg_market_coins.pack(side="left")

        self.seg_market_period = ctk.CTkSegmentedButton(
            top, values=["1д", "1н", "1м"],
            command=self._on_mkt_period_changed, width=120,
            corner_radius=8, fg_color=CARD_BG, selected_color=BORDER_COLOR
        )
        self.seg_market_period.set("1д")
        self.seg_market_period.pack(side="left", padx=15)

        ctk.CTkButton(
            top, text="🔄 Обновить рынок", command=self.trigger_chart_refresh,
            width=130, corner_radius=8, fg_color=CARD_BG, hover_color=BORDER_COLOR
        ).pack(side="right")

        self.lbl_market_cur_info = ctk.CTkLabel(
            frame, text="Загрузка реального графика Bybit...",
            font=ctk.CTkFont(size=13, weight="bold"), text_color=ACCENT_BLUE
        )
        self.lbl_market_cur_info.pack(anchor="w", padx=10, pady=2)

        self.fig_mkt, self.ax_mkt = plt.subplots(figsize=(8, 4.5), dpi=100, facecolor=CARD_BG)
        self.ax_mkt.set_facecolor(CARD_BG)
        self.canvas_mkt = FigureCanvasTkAgg(self.fig_mkt, master=frame)
        self.canvas_mkt.get_tk_widget().pack(fill="both", expand=True, padx=6, pady=6)

        self.cursor_line = self.ax_mkt.axvline(x=0, color="#ffffff", linestyle="--", linewidth=0.7, alpha=0.3)
        self.cursor_line.set_visible(False)
        self.annot = self.ax_mkt.annotate(
            "", xy=(0, 0), xytext=(-120, 15), textcoords="offset points",
            bbox=dict(boxstyle="round,pad=0.5,rounding_size=0.3", fc=CARD_INNER, ec=BORDER_COLOR, lw=1, alpha=0.95),
            color=TEXT_WHITE, fontsize=10
        )
        self.annot.set_visible(False)
        self.canvas_mkt.mpl_connect("motion_notify_event", self._on_mkt_chart_hover)
        return frame

    def _on_mkt_coin_changed(self, val):
        self.selected_sym = PAIR_MAP.get(val, "BTCUSDT")
        self.trigger_chart_refresh()

    def _on_mkt_period_changed(self, val):
        self.selected_period = val
        self.trigger_chart_refresh()

    def trigger_chart_refresh(self):
        threading.Thread(target=self._fetch_mkt_kline_worker, daemon=True).start()

    def _fetch_mkt_kline_worker(self):
        try:
            interval, limit = TIMEFRAME_MAP.get(self.selected_period, ("15", 96))
            url = f"https://api.bybit.com/v5/market/kline?category=spot&symbol={self.selected_sym}&interval={interval}&limit={limit}"
            res = requests.get(url, headers=HEADERS, timeout=5).json()

            if res.get("retCode") == 0 and self.is_running:
                raw = res.get("result", {}).get("list", [])[::-1]
                if not raw:
                    return

                df = pd.DataFrame(raw, columns=["time", "open", "high", "low", "close", "volume", "turnover"])
                utc_dt = pd.to_datetime(df["time"].astype(int), unit="ms", utc=True)
                df["time"] = utc_dt.dt.tz_convert(None)
                df["close"] = df["close"].astype(float)

                self.chart_df = df
                self.chart_x_nums = mdates.date2num(df["time"].dt.to_pydatetime())
                self.last_hover_idx = -1
                self.safe_gui(self._render_clean_market_chart, df)
        except Exception as e:
            print(f"[MARKET FETCH ERROR]: {e}")

    def _render_clean_market_chart(self, df):
        if not self.is_running:
            return
        self.ax_mkt.clear()
        self.ax_mkt.set_facecolor(CARD_BG)

        self.ax_mkt.plot(df["time"], df["close"], color=ACCENT_BLUE, linewidth=1.8, label=self.selected_sym)
        self.ax_mkt.fill_between(df["time"], df["close"], df["close"].min() * 0.999, color=ACCENT_BLUE, alpha=0.08)

        curr_p = df["close"].iloc[-1]
        self.ax_mkt.grid(True, color=BORDER_COLOR, linestyle="-", linewidth=0.5, alpha=0.6)
        self.ax_mkt.tick_params(colors=TEXT_MUTED, labelsize=8)

        if self.selected_period == "1д":
            self.ax_mkt.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        else:
            self.ax_mkt.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))

        self.ax_mkt.legend(loc="upper left", facecolor=CARD_INNER, edgecolor="none", fontsize=8)

        self.cursor_line = self.ax_mkt.axvline(x=df["time"].iloc[-1], color="#ffffff", linestyle="--", linewidth=0.7, alpha=0.25)
        self.cursor_line.set_visible(False)
        self.annot = self.ax_mkt.annotate(
            "", xy=(0, 0), xytext=(-120, 15), textcoords="offset points",
            bbox=dict(boxstyle="round,pad=0.5,rounding_size=0.3", fc=CARD_INNER, ec=BORDER_COLOR, lw=1, alpha=0.95),
            color=TEXT_WHITE, fontsize=10
        )
        self.annot.set_visible(False)

        self.lbl_market_cur_info.configure(text=f"● {self.selected_sym}  |  Текущая цена: ${curr_p:,.2f}")
        self.fig_mkt.tight_layout()
        self.canvas_mkt.draw_idle()

    def _on_mkt_chart_hover(self, event):
        if not self.is_running or event.inaxes != self.ax_mkt or self.chart_df is None or self.chart_x_nums is None:
            if self.cursor_line.get_visible():
                self.cursor_line.set_visible(False)
                self.annot.set_visible(False)
                self.canvas_mkt.draw_idle()
            return

        idx = np.searchsorted(self.chart_x_nums, event.xdata)
        idx = np.clip(idx, 0, len(self.chart_df) - 1)

        if idx == self.last_hover_idx:
            return
        self.last_hover_idx = idx

        row = self.chart_df.iloc[idx]
        self.cursor_line.set_xdata([row["time"]])
        self.cursor_line.set_visible(True)

        dt_str = row["time"].strftime("%d/%m %H:%M")
        self.lbl_market_cur_info.configure(text=f"📍 {self.selected_sym}  |  ${row['close']:,.2f}  |  {dt_str}")

        xlim = self.ax_mkt.get_xlim()
        midpoint = xlim[0] + (xlim - xlim[0]) * 0.65
        if event.xdata > midpoint:
            self.annot.set_position((-130, 15))
        else:
            self.annot.set_position((15, 15))

        self.annot.xy = (mdates.date2num(row["time"]), row["close"])
        self.annot.set_text(f"${row['close']:,.2f}\n{dt_str}")
        self.annot.set_visible(True)
        self.canvas_mkt.draw_idle()

    # ================= 5. РУЧНАЯ ТОРГОВЛЯ =================
    def _build_view_manual(self):
        frame = ctk.CTkScrollableFrame(self.views_container, fg_color="transparent")

        card = ctk.CTkFrame(frame, corner_radius=12, fg_color=CARD_BG, border_width=1, border_color=BORDER_COLOR)
        card.pack(fill="x", padx=15, pady=10)

        ctk.CTkLabel(card, text="⇄ Ручной перехват и торговля с любой суммой ордера", font=ctk.CTkFont(size=14, weight="bold"), text_color=TEXT_WHITE).pack(padx=15, pady=(12, 2), anchor="w")
        ctk.CTkLabel(card, text="Сделки синхронизируются с сервером и учитывают проскальзывание 0.05%.", font=ctk.CTkFont(size=11), text_color=TEXT_MUTED).pack(padx=15, pady=(0, 12), anchor="w")

        self.manual_inputs = {}
        for pair in ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT"]:
            row = ctk.CTkFrame(frame, fg_color=CARD_BG, corner_radius=10, border_width=1, border_color=BORDER_COLOR)
            row.pack(fill="x", padx=15, pady=4)

            ctk.CTkLabel(row, text=f"🪙 {pair}", font=ctk.CTkFont(size=13, weight="bold"), text_color=TEXT_WHITE, width=110, anchor="w").pack(side="left", padx=15, pady=10)
            ctk.CTkLabel(row, text="Сумма $:", font=ctk.CTkFont(size=11), text_color=TEXT_MUTED).pack(side="left", padx=(5, 2))
            amt_entry = ctk.CTkEntry(row, width=80, height=30, fg_color=CARD_INNER, border_color=BORDER_COLOR)
            amt_entry.insert(0, "100.0")
            amt_entry.pack(side="left", padx=4)
            self._attach_menu(amt_entry)
            self.manual_inputs[pair] = amt_entry

            for pct in [25, 50, 100]:
                btn_pct = ctk.CTkButton(
                    row, text=f"{pct}%", width=40, height=26, corner_radius=6,
                    fg_color=CARD_INNER, hover_color=BORDER_COLOR, font=ctk.CTkFont(size=9),
                    command=lambda p=pair, pr=pct: self._set_quick_pct(p, pr)
                )
                btn_pct.pack(side="left", padx=2)

            btn_b = ctk.CTkButton(
                row, text="🟢 Купить", width=100, height=30, corner_radius=8,
                fg_color="#1c3b2b", text_color=NEON_GREEN, font=ctk.CTkFont(weight="bold"),
                command=lambda p=pair: self._execute_manual_trade(p, "BUY")
            )
            btn_b.pack(side="left", padx=10)

            btn_s = ctk.CTkButton(
                row, text="🔴 Закрыть", width=100, height=30, corner_radius=8,
                fg_color="#3d1c1c", text_color=NEON_RED, font=ctk.CTkFont(weight="bold"),
                command=lambda p=pair: self._execute_manual_trade(p, "CLOSE")
            )
            btn_s.pack(side="left", padx=4)

        return frame

    def _set_quick_pct(self, pair, pct):
        cash = float(self.server_data.get("cash", 1000.0))
        alloc = round((cash * pct) / 100.0, 2)
        entry = self.manual_inputs.get(pair)
        if entry:
            entry.delete(0, "end")
            entry.insert(0, str(alloc))

    def _execute_manual_trade(self, pair, action):
        code = pair.replace("/", "")
        p = self.market_prices.get(code, {}).get("price", 100.0)

        try:
            amount_usdt = float(self.manual_inputs[pair].get().strip())
        except Exception:
            amount_usdt = 100.0

        st = self.server_data
        if action == "BUY":
            if st["cash"] >= amount_usdt and pair not in st.get("positions", {}):
                st["cash"] -= amount_usdt
                st["positions"][pair] = {
                    "amount": round((amount_usdt * 0.999) / p, 6),
                    "entry_price": round(p * 1.0005, 4), # Slippage 0.05%
                    "stop_loss": round(p * 0.98, 4),
                    "opened_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
        elif action == "CLOSE":
            if pair in st.get("positions", {}):
                pos = st["positions"][pair]
                st["cash"] += round(pos["amount"] * p * 0.999, 2)
                del st["positions"][pair]

        self._save_state_to_vps(st)
        self._render_active_positions()

    # ================= 6. БЭКТЕСТИНГ =================
    def _build_view_backtest(self):
        frame = ctk.CTkFrame(self.views_container, fg_color="transparent")

        top = ctk.CTkFrame(frame, fg_color=CARD_BG, corner_radius=10, border_width=1, border_color=BORDER_COLOR)
        top.pack(fill="x", pady=6, padx=4)

        r1 = ctk.CTkFrame(top, fg_color="transparent")
        r1.pack(fill="x", padx=10, pady=(8, 4))

        ctk.CTkLabel(r1, text="Инструмент:", font=ctk.CTkFont(size=11, weight="bold"), text_color=TEXT_MUTED).pack(side="left", padx=(0, 4))
        self.bt_sym = ctk.CTkOptionMenu(
            r1, values=["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT"], width=110,
            corner_radius=8, fg_color=CARD_INNER, button_color=BORDER_COLOR
        )
        self.bt_sym.set("BTC/USDT")
        self.bt_sym.pack(side="left", padx=4)

        ctk.CTkLabel(r1, text="Таймфрейм:", font=ctk.CTkFont(size=11, weight="bold"), text_color=TEXT_MUTED).pack(side="left", padx=(10, 4))
        self.bt_tf = ctk.CTkOptionMenu(
            r1, values=["15m (300 свечей)", "1h (500 свечей)", "4h (500 свечей)"], width=150,
            corner_radius=8, fg_color=CARD_INNER, button_color=BORDER_COLOR
        )
        self.bt_tf.set("1h (500 свечей)")
        self.bt_tf.pack(side="left", padx=4)

        self.btn_run_bt = ctk.CTkButton(
            r1, text="🚀 Запустить симуляцию", corner_radius=8, fg_color=ACCENT_BLUE,
            command=self._launch_backtest_thread
        )
        self.btn_run_bt.pack(side="right", padx=6)

        r2 = ctk.CTkFrame(top, fg_color="transparent")
        r2.pack(fill="x", padx=10, pady=(4, 8))

        ctk.CTkLabel(r2, text="EMA:", font=ctk.CTkFont(size=11), text_color=TEXT_MUTED).pack(side="left", padx=(0, 2))
        self.bt_in_ema = ctk.CTkEntry(r2, width=55, height=28, fg_color=CARD_INNER, border_color=BORDER_COLOR)
        self.bt_in_ema.insert(0, "50")
        self.bt_in_ema.pack(side="left", padx=4)

        ctk.CTkLabel(r2, text="Канал Дончиана:", font=ctk.CTkFont(size=11), text_color=TEXT_MUTED).pack(side="left", padx=(8, 2))
        self.bt_in_don = ctk.CTkEntry(r2, width=55, height=28, fg_color=CARD_INNER, border_color=BORDER_COLOR)
        self.bt_in_don.insert(0, "10")
        self.bt_in_don.pack(side="left", padx=4)

        ctk.CTkLabel(r2, text="Стоп-Лосс (%):", font=ctk.CTkFont(size=11), text_color=TEXT_MUTED).pack(side="left", padx=(8, 2))
        self.bt_in_sl = ctk.CTkEntry(r2, width=55, height=28, fg_color=CARD_INNER, border_color=BORDER_COLOR)
        self.bt_in_sl.insert(0, "2.0")
        self.bt_in_sl.pack(side="left", padx=4)

        ctk.CTkLabel(r2, text="Вход ($):", font=ctk.CTkFont(size=11), text_color=TEXT_MUTED).pack(side="left", padx=(8, 2))
        self.bt_in_size = ctk.CTkEntry(r2, width=65, height=28, fg_color=CARD_INNER, border_color=BORDER_COLOR)
        self.bt_in_size.insert(0, "200.0")
        self.bt_in_size.pack(side="left", padx=4)

        self.bt_metrics_row = ctk.CTkFrame(frame, corner_radius=10, fg_color=CARD_BG, border_width=1, border_color=BORDER_COLOR)
        self.bt_metrics_row.pack(fill="x", pady=4, padx=4)
        self.bt_metrics_row.grid_columnconfigure((0, 1, 2, 3), weight=1)

        self.kpi_bt_bot = self._create_kpi_box(self.bt_metrics_row, "ДОХОД СТРАТЕГИИ", "--", 0)
        self.kpi_bt_mkt = self._create_kpi_box(self.bt_metrics_row, "BUY & HOLD (РЫНОК)", "--", 1)
        self.kpi_bt_dd = self._create_kpi_box(self.bt_metrics_row, "МАКС. ПРОСАДКА", "--", 2)
        self.kpi_bt_wr = self._create_kpi_box(self.bt_metrics_row, "ВИНРЕЙТ СДЕЛОК", "--", 3)

        self.fig_bt, self.ax_bt = plt.subplots(figsize=(8, 4), dpi=100, facecolor=CARD_BG)
        self.ax_bt.set_facecolor(CARD_BG)
        self.canvas_bt = FigureCanvasTkAgg(self.fig_bt, master=frame)
        self.canvas_bt.get_tk_widget().pack(fill="both", expand=True, padx=6, pady=6)
        return frame

    def _launch_backtest_thread(self):
        threading.Thread(target=self._run_historical_backtest, daemon=True).start()

    def _run_historical_backtest(self):
        self.safe_gui(self.btn_run_bt.configure, text="Расчет...", state="disabled")
        raw_sym = self.bt_sym.get()
        sym = PAIR_MAP.get(raw_sym, "BTCUSDT")
        sel_tf = self.bt_tf.get()
        interval = "60" if "1h" in sel_tf else ("240" if "4h" in sel_tf else "15")

        try:
            ema_p = int(self.bt_in_ema.get().strip())
            don_p = int(self.bt_in_don.get().strip())
            sl_pct = float(self.bt_in_sl.get().strip())
            alloc = float(self.bt_in_size.get().strip())
        except Exception:
            ema_p, don_p, sl_pct, alloc = 50, 10, 2.0, 200.0

        try:
            url = f"https://api.bybit.com/v5/market/kline?category=spot&symbol={sym}&interval={interval}&limit=500"
            res = requests.get(url, headers=HEADERS, timeout=6).json()
            raw = res.get("result", {}).get("list", [])[::-1]

            df = pd.DataFrame(raw, columns=["time", "open", "high", "low", "close", "volume", "turnover"])
            df["time"] = pd.to_datetime(df["time"].astype(int), unit="ms")
            for c in ["open", "high", "low", "close"]:
                df[c] = df[c].astype(float)

            df["ema"] = df["close"].ewm(span=ema_p, adjust=False).mean()
            df["upper"] = df["high"].rolling(don_p).max()

            cash = 1000.0
            position = None
            equity_curve = []
            trades = []

            start_idx = max(ema_p, don_p)
            for i in range(start_idx, len(df)):
                price = df["close"].iloc[i]
                if position:
                    if price <= position["stop"] or price < df["ema"].iloc[i]:
                        net = (position["amount"] * price) * 0.999
                        cash += net
                        trades.append(net - position["cost"])
                        position = None
                elif df["close"].iloc[i - 1] > df["upper"].iloc[i - 2] and df["close"].iloc[i - 1] > df["ema"].iloc[i - 1]:
                    if cash >= alloc:
                        amt = (alloc * 0.999) / price
                        cash -= alloc
                        position = {
                            "amount": amt,
                            "cost": alloc,
                            "stop": price * (1.0 - (sl_pct / 100.0))
                        }

                equity_curve.append(cash + (position["amount"] * price if position else 0.0))

            bot_ret = ((equity_curve[-1] - 1000.0) / 1000.0) * 100
            mkt_ret = ((df["close"].iloc[-1] - df["close"].iloc[start_idx]) / df["close"].iloc[start_idx]) * 100
            peak = np.maximum.accumulate(equity_curve)
            max_dd = np.max(((peak - equity_curve) / peak) * 100) if len(peak) > 0 else 0.0
            win_rate = (len([t for t in trades if t > 0]) / len(trades) * 100) if trades else 0.0

            self.safe_gui(
                self._render_bt_chart_ui,
                df["time"].iloc[start_idx:],
                equity_curve,
                df["close"].iloc[start_idx:],
                bot_ret,
                mkt_ret,
                max_dd,
                win_rate
            )
        except Exception as e:
            print(f"[BT ERR]: {e}")
        finally:
            self.safe_gui(self.btn_run_bt.configure, text="🚀 Запустить симуляцию", state="normal")

    def _render_bt_chart_ui(self, dates, bot_eq, prices, bot_ret, mkt_ret, max_dd, win_rate):
        c_b = NEON_GREEN if bot_ret >= 0 else NEON_RED
        c_m = NEON_GREEN if mkt_ret >= 0 else NEON_RED

        self.kpi_bt_bot.configure(text=f"{bot_ret:+.2f}%", text_color=c_b)
        self.kpi_bt_mkt.configure(text=f"{mkt_ret:+.2f}%", text_color=c_m)
        self.kpi_bt_dd.configure(text=f"-{max_dd:.2f}%", text_color=NEON_RED)
        self.kpi_bt_wr.configure(text=f"{win_rate:.1f}%")

        mkt_curve = 1000.0 * (prices / prices.iloc[0])
        self.ax_bt.clear()
        self.ax_bt.set_facecolor(CARD_BG)

        self.ax_bt.plot(dates, bot_eq, color=ACCENT_BLUE, linewidth=2.0, label="Стратегия Бота")
        self.ax_bt.plot(dates, mkt_curve, color=TEXT_MUTED, linewidth=1.2, linestyle="--", label="Купил и держи (Рынок)")

        self.ax_bt.grid(True, color=BORDER_COLOR, linestyle="-", linewidth=0.5)
        self.ax_bt.tick_params(colors=TEXT_MUTED, labelsize=8)
        self.ax_bt.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
        self.ax_bt.legend(loc="upper left", facecolor=CARD_INNER, edgecolor="none")
        self.fig_bt.tight_layout()
        self.canvas_bt.draw_idle()

    # ================= 7. ТЕРМИНАЛ VPS =================
    def _build_view_terminal(self):
        frame = ctk.CTkFrame(self.views_container, fg_color="transparent")
        card = ctk.CTkFrame(frame, corner_radius=12, fg_color=CARD_BG, border_width=1, border_color=BORDER_COLOR)
        card.pack(fill="both", expand=True, padx=15, pady=10)

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=15, pady=(12, 6))
        ctk.CTkLabel(top, text="💻 Прямой консольный доступ к серверу VPS", font=ctk.CTkFont(size=14, weight="bold"), text_color=TEXT_WHITE).pack(side="left")

        self.txt_full_console = ctk.CTkTextbox(card, fg_color=CARD_INNER, font=ctk.CTkFont(family="Consolas", size=11))
        self.txt_full_console.pack(fill="both", expand=True, padx=15, pady=10)
        self.txt_full_console.insert("end", "=== VPS TERMINAL INITIALIZED ===\nГотов к приему команд сервера...\n\n")

        cmd_box = ctk.CTkFrame(card, fg_color="transparent")
        cmd_box.pack(fill="x", padx=15, pady=(0, 15))

        self.in_custom_bash = ctk.CTkEntry(cmd_box, placeholder_text="Введите команду (например: ps aux | grep symulator)", fg_color=CARD_INNER, border_color=BORDER_COLOR)
        self.in_custom_bash.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self._attach_menu(self.in_custom_bash)

        ctk.CTkButton(cmd_box, text="Выполнить", width=110, corner_radius=8, fg_color=ACCENT_BLUE, command=self._run_bash_command).pack(side="right")
        return frame

    def _run_bash_command(self):
        cmd = self.in_custom_bash.get().strip()
        if not (cmd and self.server_connected):
            return

        def worker():
            try:
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                client.connect(hostname=self.current_server_ip, port=self.current_server_port, username="root", password=self.current_server_pwd, timeout=5)
                _, stdout, stderr = client.exec_command(cmd)
                out = stdout.read().decode("utf-8") + stderr.read().decode("utf-8")
                client.close()
                self.safe_gui(self.txt_full_console.insert, "end", f"\n$ {cmd}\n{out}\n" + "-" * 60 + "\n")
                self.safe_gui(self.txt_full_console.see, "end")
            except Exception as e:
                self.safe_gui(self.txt_full_console.insert, "end", f"Ошибка: {e}\n")

        threading.Thread(target=worker, daemon=True).start()

    # ================= 8. АНАЛИТИКА И ЖУРНАЛ =================
    def _build_view_analytics(self):
        frame = ctk.CTkScrollableFrame(self.views_container, fg_color="transparent")

        card = ctk.CTkFrame(frame, corner_radius=12, fg_color=CARD_BG, border_width=1, border_color=BORDER_COLOR)
        card.pack(fill="x", padx=15, pady=10)

        ctk.CTkLabel(card, text="📈 Аналитика показателей торгового журнала", font=ctk.CTkFont(size=14, weight="bold"), text_color=TEXT_WHITE).pack(padx=15, pady=(15, 6), anchor="w")

        grid = ctk.CTkFrame(card, fg_color="transparent")
        grid.pack(fill="x", padx=15, pady=10)
        grid.grid_columnconfigure((0, 1, 2, 3), weight=1)

        self.an_trades_count = self._create_kpi_box(grid, "ВСЕГО СДЕЛОК", "0", 0, TEXT_WHITE)
        self.an_winrate = self._create_kpi_box(grid, "ВИНРЕЙТ (%)", "0.0%", 1, NEON_GREEN)
        self.an_net_pnl = self._create_kpi_box(grid, "ЧИСТАЯ ПРИБЫЛЬ ($)", "$0.00", 2, NEON_GREEN)
        self.an_profit_factor = self._create_kpi_box(grid, "ПРОФИТ-ФАКТОР", "0.00", 3, ACCENT_BLUE)

        self.lbl_analytics_details = ctk.CTkLabel(card, text="Загрузка данных торгового аудита с VPS...", font=ctk.CTkFont(size=12), text_color=TEXT_MUTED)
        self.lbl_analytics_details.pack(padx=15, pady=(10, 15), anchor="w")
        return frame

    def _calculate_and_render_analytics(self):
        if not self.audit_raw.strip():
            self.lbl_analytics_details.configure(text="Журнал пуст. Бот ещё не закрыл ни одной сделки на сервере.")
            return

        try:
            reader = csv.reader(io.StringIO(self.audit_raw))
            rows = list(reader)
            if len(rows) <= 1:
                return

            pnls = []
            for r in rows[1:]:
                if len(r) >= 5:
                    try:
                        val = float(r[4].replace("$", "").replace("+", "").strip())
                        if abs(val) > 0.0001:
                            pnls.append(val)
                    except Exception:
                        pass

            if not pnls:
                self.lbl_analytics_details.configure(text="Все сделки пока находятся в процессе или открыты.")
                return

            total_trades = len(pnls)
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p < 0]
            winrate = (len(wins) / total_trades) * 100.0 if total_trades > 0 else 0.0
            net_pnl = sum(pnls)

            gross_profit = sum(wins)
            gross_loss = abs(sum(losses))
            pf = (gross_profit / gross_loss) if gross_loss > 0 else gross_profit

            self.an_trades_count.configure(text=str(total_trades))
            self.an_winrate.configure(text=f"{winrate:.1f}%")
            self.an_net_pnl.configure(text=f"${net_pnl:+,.2f}", text_color=NEON_GREEN if net_pnl >= 0 else NEON_RED)
            self.an_profit_factor.configure(text=f"{pf:.2f}")

            self.lbl_analytics_details.configure(
                text=f"Прибыльных сделок: {len(wins)} | Убыточных: {len(losses)} | Сумма валовой прибыли: ${gross_profit:,.2f} | Валовый убыток: ${gross_loss:,.2f}"
            )
        except Exception as e:
            self.lbl_analytics_details.configure(text=f"Ошибка расчета журнала: {e}")

    # ================= 9. РИСК-МЕНЕДЖМЕНТ =================
    def _build_view_risk(self):
        frame = ctk.CTkScrollableFrame(self.views_container, fg_color="transparent")

        card = ctk.CTkFrame(frame, corner_radius=12, fg_color=CARD_BG, border_width=1, border_color=BORDER_COLOR)
        card.pack(fill="x", padx=15, pady=10)

        ctk.CTkLabel(card, text="🛡️ Интерактивные параметры риска и логики стратегии", font=ctk.CTkFont(size=14, weight="bold"), text_color=TEXT_WHITE).pack(padx=15, pady=(15, 2), anchor="w")
        ctk.CTkLabel(card, text="Параметры строго соответствуют институциональному ТЗ (ATR, EMA50, Donchian 10).", font=ctk.CTkFont(size=11), text_color=TEXT_MUTED).pack(padx=15, pady=(0, 15), anchor="w")

        f1 = ctk.CTkFrame(card, fg_color="transparent")
        f1.pack(fill="x", padx=15, pady=6)
        ctk.CTkLabel(f1, text="Период скользящей средней EMA:", font=ctk.CTkFont(size=12), text_color=TEXT_WHITE, width=320, anchor="w").pack(side="left")
        self.in_ema_period = ctk.CTkEntry(f1, width=80, fg_color=CARD_INNER, border_color=BORDER_COLOR)
        self.in_ema_period.insert(0, str(self.risk_settings.get("ema_period", 50)))
        self.in_ema_period.pack(side="left", padx=10)

        f2 = ctk.CTkFrame(card, fg_color="transparent")
        f2.pack(fill="x", padx=15, pady=6)
        ctk.CTkLabel(f2, text="Период канала Дончиана (выход):", font=ctk.CTkFont(size=12), text_color=TEXT_WHITE, width=320, anchor="w").pack(side="left")
        self.in_don_period = ctk.CTkEntry(f2, width=80, fg_color=CARD_INNER, border_color=BORDER_COLOR)
        self.in_don_period.insert(0, str(self.risk_settings.get("donchian_period", 10)))
        self.in_don_period.pack(side="left", padx=10)

        f3 = ctk.CTkFrame(card, fg_color="transparent")
        f3.pack(fill="x", padx=15, pady=6)
        ctk.CTkLabel(f3, text="Множитель стопа волатильности (x ATR):", font=ctk.CTkFont(size=12), text_color=TEXT_WHITE, width=320, anchor="w").pack(side="left")
        self.in_sl_pct = ctk.CTkEntry(f3, width=80, fg_color=CARD_INNER, border_color=BORDER_COLOR)
        self.in_sl_pct.insert(0, str(self.risk_settings.get("stop_loss_pct", 2.0)))
        self.in_sl_pct.pack(side="left", padx=10)

        f4 = ctk.CTkFrame(card, fg_color="transparent")
        f4.pack(fill="x", padx=15, pady=6)
        ctk.CTkLabel(f4, text="Максимальная дневная просадка (Max DD %):", font=ctk.CTkFont(size=12), text_color=TEXT_WHITE, width=320, anchor="w").pack(side="left")
        self.in_max_dd = ctk.CTkEntry(f4, width=80, fg_color=CARD_INNER, border_color=BORDER_COLOR)
        self.in_max_dd.insert(0, str(self.risk_settings.get("max_daily_dd", 10.0)))
        self.in_max_dd.pack(side="left", padx=10)

        btn_save_risk = ctk.CTkButton(
            card, text="💾 Сохранить параметры риска на VPS", width=260, height=38,
            corner_radius=8, fg_color=ACCENT_BLUE, command=self._save_risk_settings_to_server
        )
        btn_save_risk.pack(padx=15, pady=(15, 10), anchor="w")

        self.lbl_risk_save_status = ctk.CTkLabel(card, text="", font=ctk.CTkFont(size=11), text_color=NEON_GREEN)
        self.lbl_risk_save_status.pack(padx=15, pady=(0, 15), anchor="w")
        return frame

    def _save_risk_settings_to_server(self):
        try:
            self.risk_settings["ema_period"] = int(self.in_ema_period.get().strip())
            self.risk_settings["donchian_period"] = int(self.in_don_period.get().strip())
            self.risk_settings["stop_loss_pct"] = float(self.in_sl_pct.get().strip())
            self.risk_settings["max_daily_dd"] = float(self.in_max_dd.get().strip())

            self.lbl_risk_summary_text.configure(text=f"Лимит: {self.risk_settings['max_daily_dd']}% Max DD")

            with open(BOT_RISK_FILE, "w") as f:
                json.dump(self.risk_settings, f, indent=4)

            self.lbl_risk_save_status.configure(text="✓ Параметры сохранены!", text_color=NEON_GREEN)
        except Exception as e:
            self.lbl_risk_save_status.configure(text=f"Ошибка: {e}", text_color=NEON_RED)

    # ================= 10. НАСТРОЙКИ =================
    def _build_view_settings(self):
        frame = ctk.CTkScrollableFrame(self.views_container, fg_color="transparent")

        box_theme = ctk.CTkFrame(frame, corner_radius=12, fg_color=CARD_BG, border_width=1, border_color=BORDER_COLOR)
        box_theme.pack(fill="x", padx=15, pady=8)

        ctk.CTkLabel(box_theme, text="🎨 ТЕМА ИНТЕРФЕЙСА", font=ctk.CTkFont(size=13, weight="bold"), text_color=TEXT_WHITE).pack(padx=15, pady=(12, 4), anchor="w")
        self.seg_theme = ctk.CTkSegmentedButton(
            box_theme, values=["Тёмная (Dark Glass)", "Светлая (Light Minimal)"],
            command=self._change_app_theme, corner_radius=8, fg_color=CARD_INNER, selected_color=BORDER_COLOR
        )
        self.seg_theme.set("Тёмная (Dark Glass)")
        self.seg_theme.pack(padx=15, pady=(4, 15), anchor="w")

        box_tg = ctk.CTkFrame(frame, corner_radius=12, fg_color=CARD_BG, border_width=1, border_color=BORDER_COLOR)
        box_tg.pack(fill="x", padx=15, pady=8)

        ctk.CTkLabel(box_tg, text="🔔 ОПОВЕЩЕНИЯ В TELEGRAM", font=ctk.CTkFont(size=13, weight="bold"), text_color=TEXT_WHITE).pack(padx=15, pady=(12, 4), anchor="w")
        btn_tg = ctk.CTkButton(
            box_tg, text="Отправить тестовое сообщение в Telegram", corner_radius=8,
            fg_color=CARD_INNER, hover_color=BORDER_COLOR, command=self._send_test_tg_message
        )
        btn_tg.pack(padx=15, pady=(4, 15), anchor="w")

        btn_logout = ctk.CTkButton(
            frame, text="🚪 Выйти из текущего сервера (на экран входа)", corner_radius=8,
            fg_color=PANIC_RED, hover_color="#7f1d1d", height=38, command=self._exit_to_login
        )
        btn_logout.pack(padx=15, pady=(10, 20), anchor="w")
        return frame

    def _change_app_theme(self, val):
        if "Светлая" in val:
            ctk.set_appearance_mode("light")
        else:
            ctk.set_appearance_mode("dark")

    def _send_test_tg_message(self):
        def worker():
            try:
                url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
                requests.post(url, json={
                    "chat_id": TG_CHAT_ID,
                    "text": "🔔 <b>Тест из Торгового Терминала!</b> Связь с ботом настроена.",
                    "parse_mode": "HTML"
                }, timeout=4)
            except Exception:
                pass
        threading.Thread(target=worker, daemon=True).start()

    def _exit_to_login(self):
        self.server_connected = False
        self.main_app_view.pack_forget()
        self.login_view.pack(fill="both", expand=True)

    # ================= 11. УПРАВЛЕНИЕ И СДЕЛКИ =================
    def _toggle_bot_state(self):
        """Интеллектуальный запуск / остановка бота с мягким сигналом SIGINT."""
        if not self.server_connected:
            return

        if not self.bot_active:
            # Запуск бота на сервере в tmux
            self.btn_bot_toggle.configure(text="Запуск...", state="disabled")
            cmd = "tmux kill-session -t bot 2>/dev/null; tmux new-session -d -s bot 'python3 symulator.py'"
            self._execute_remote_ssh(cmd)
            self.after(1500, lambda: self.btn_bot_toggle.configure(state="normal"))
        else:
            # Мягкая остановка бота через SIGINT, чтобы успело уйти уведомление в Telegram
            self.btn_bot_toggle.configure(text="Остановка...", state="disabled")
            cmd = "pkill -INT -f symulator.py || tmux kill-session -t bot 2>/dev/null"
            self._execute_remote_ssh(cmd)
            self.after(1500, lambda: self.btn_bot_toggle.configure(state="normal"))

    def _panic_emergency_stop(self):
        """Экстренное закрытие всех позиций и глушение бота."""
        cmd = "pkill -INT -f symulator.py || tmux kill-session -t bot 2>/dev/null"
        self._execute_remote_ssh(cmd)

        st = self.server_data
        if st.get("positions"):
            for sym, pos in list(st["positions"].items()):
                code = sym.replace("/", "")
                p = self.market_prices.get(code, {}).get("price", pos["entry_price"])
                st["cash"] += pos["amount"] * p * 0.999
            st["positions"] = {}
            st["status"] = "STOPPED"
            self._save_state_to_vps(st)
            self._render_active_positions()

    def _save_state_to_vps(self, st):
        if not self.server_connected:
            return
        def worker():
            try:
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                client.connect(hostname=self.current_server_ip, port=self.current_server_port, username="root", password=self.current_server_pwd, timeout=4)
                sftp = client.open_sftp()
                with sftp.open("bot_state.json", "w") as f:
                    json.dump(st, f, indent=4)
                sftp.close()
                client.close()
                self._fetch_vps_files()
            except Exception:
                pass
        threading.Thread(target=worker, daemon=True).start()

    def _execute_remote_ssh(self, cmd):
        if not self.server_connected:
            return
        def worker():
            try:
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                client.connect(hostname=self.current_server_ip, port=self.current_server_port, username="root", password=self.current_server_pwd, timeout=4)
                client.exec_command(cmd)
                client.close()
            except Exception:
                pass
        threading.Thread(target=worker, daemon=True).start()

    # ================= 12. ФОНОВЫЙ ОПРОС И РЕНДЕР =================
    def _uptime_timer_worker(self):
        while self.is_running:
            elapsed = int(time.time() - self.start_time)
            days = elapsed // 86400
            hours = (elapsed % 86400) // 3600
            mins = (elapsed % 3600) // 60
            secs = elapsed % 60
            self.safe_gui(self.card_bot_uptime.configure, text=f"{days:02d}d {hours:02d}h {mins:02d}m {secs:02d}s")
            time.sleep(1)

    def _market_data_worker(self):
        while self.is_running:
            try:
                res = requests.get("https://api.bybit.com/v5/market/tickers?category=spot", headers=HEADERS, timeout=4).json()
                if res.get("retCode") == 0 and self.is_running:
                    for item in res.get("result", {}).get("list", []):
                        sym = item.get("symbol")
                        if sym in WATCH_PAIRS:
                            self.market_prices[sym] = {
                                "price": float(item.get("lastPrice", 0)),
                                "change": float(item.get("price24hPcnt", 0)) * 100
                            }
                    btc_p = self.market_prices.get("BTCUSDT", {}).get("price", 76000.0)
                    self.safe_gui(self.lbl_mini_btc.configure, text=f"${btc_p:,.2f}")

                if self.server_connected:
                    self._fetch_vps_files()
                else:
                    self.safe_gui(self._render_active_positions)
            except Exception:
                pass
            time.sleep(4)

    def _fetch_vps_files(self):
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(hostname=self.current_server_ip, port=self.current_server_port, username="root", password=self.current_server_pwd, timeout=4)
            sftp = client.open_sftp()
            try:
                with sftp.open("bot_state.json", "r") as f:
                    self.server_data = json.load(f)
            except Exception:
                pass
            try:
                with sftp.open("trade_audit.csv", "r") as f:
                    self.audit_raw = f.read().decode("utf-8")
            except Exception:
                pass
            sftp.close()
            client.close()

            self.safe_gui(self._render_active_positions)
        except Exception:
            pass

    def _render_active_positions(self):
        cash = float(self.server_data.get("cash", 1000.0))
        init_bal = float(self.server_data.get("initial_balance", 1000.0))
        positions = self.server_data.get("positions", {})
        bot_status = self.server_data.get("status", "STOPPED")

        # Автоматическая синхронизация кнопки старт/стоп с реальным статусом на VPS
        if bot_status == "RUNNING":
            self.bot_active = True
            self.btn_bot_toggle.configure(text="⏸️ Остановить бота", fg_color="#3d1c1c", hover_color="#7f1d1d")
            if self.server_connected:
                self.hdr_server_badge.configure(text=f"● БОТ РАБОТАЕТ ({self.current_server_ip})", text_color=NEON_GREEN)
        else:
            self.bot_active = False
            self.btn_bot_toggle.configure(text="▶️ Запустить бота", fg_color="#1c3b2b", hover_color="#10b981")
            if self.server_connected:
                self.hdr_server_badge.configure(text=f"○ БОТ НА ПАУЗЕ ({self.current_server_ip})", text_color="#f59e0b")

        total_holdings = 0.0
        for w in self.pos_rows_container.winfo_children():
            w.destroy()

        hdr = ctk.CTkFrame(self.pos_rows_container, fg_color="transparent")
        hdr.pack(fill="x", pady=(2, 4))
        for title, width, anchor in [
            ("Торговая пара", 140, "w"),
            ("Цена входа", 110, "center"),
            ("Текущая цена", 110, "center"),
            ("Размер позиции", 120, "center"),
            ("Динамический PnL", 140, "center"),
            ("Take-Profit и Stop-Loss", 170, "center"),
            ("", 120, "e")
        ]:
            ctk.CTkLabel(hdr, text=title, font=ctk.CTkFont(size=10, weight="bold"), text_color=TEXT_MUTED, width=width, anchor=anchor).pack(side="left", padx=4)

        if not positions:
            e_box = ctk.CTkFrame(self.pos_rows_container, fg_color="transparent")
            e_box.pack(fill="x", pady=8)
            status_text = "● Бот запущен на VPS, ожидает пробоя уровней 1h..." if bot_status == "RUNNING" else "○ Бот на сервере остановлен."
            ctk.CTkLabel(e_box, text=f"Нет активных позиций. {status_text}", font=ctk.CTkFont(size=11), text_color=TEXT_MUTED).pack()
        else:
            for sym, pos in positions.items():
                code = sym.replace("/", "")
                curr = self.market_prices.get(code, {}).get("price") or pos["entry_price"]
                val = pos["amount"] * curr
                total_holdings += val

                pnl_d = val - (pos["amount"] * pos["entry_price"])
                pnl_p = ((curr - pos["entry_price"]) / pos["entry_price"]) * 100

                row = ctk.CTkFrame(self.pos_rows_container, corner_radius=8, fg_color=CARD_INNER, border_width=1, border_color=BORDER_COLOR, height=38)
                row.pack(fill="x", pady=2)

                icon_col = "#f59e0b" if "BTC" in sym else "#06b6d4"
                pf = ctk.CTkFrame(row, fg_color="transparent", width=140)
                pf.pack(side="left", padx=6)
                tk.Label(pf, text="●", fg=icon_col, bg=CARD_INNER, font=("", 11, "bold")).pack(side="left", padx=(4, 6))
                ctk.CTkLabel(pf, text=sym, font=ctk.CTkFont(size=12, weight="bold"), text_color=TEXT_WHITE).pack(side="left")

                ctk.CTkLabel(row, text=f"${pos['entry_price']:,.2f}", font=ctk.CTkFont(size=12), text_color=TEXT_WHITE, width=110).pack(side="left", padx=4)
                ctk.CTkLabel(row, text=f"${curr:,.2f}", font=ctk.CTkFont(size=12), text_color=TEXT_WHITE, width=110).pack(side="left", padx=4)
                ctk.CTkLabel(row, text=f"{pos['amount']:.4f}", font=ctk.CTkFont(size=12), text_color=TEXT_WHITE, width=120).pack(side="left", padx=4)

                pnl_c = NEON_GREEN if pnl_d >= 0 else NEON_RED
                ctk.CTkLabel(row, text=f"{pnl_d:+.2f}$ ({pnl_p:+.2f}%)", font=ctk.CTkFont(size=12, weight="bold"), text_color=pnl_c, width=140).pack(side="left", padx=4)

                ctk.CTkLabel(row, text=f"SL: ${pos['stop_loss']:,.2f}", font=ctk.CTkFont(size=11), text_color=TEXT_MUTED, width=170).pack(side="left", padx=4)

                ctk.CTkButton(
                    row, text="Close Position", width=110, height=26, corner_radius=6,
                    fg_color="#2b313d", hover_color=PANIC_RED, font=ctk.CTkFont(size=10, weight="bold"),
                    command=lambda s=sym: self._execute_manual_trade(s, "CLOSE")
                ).pack(side="right", padx=8)

        equity = cash + total_holdings
        tot_pnl_d = equity - init_bal
        tot_pnl_p = (tot_pnl_d / init_bal) * 100 if init_bal > 0 else 0.0

        btc_r = self.market_prices.get("BTCUSDT", {}).get("price", 76000.0)
        btc_eq = equity / btc_r if btc_r > 0 else 0.0

        self.hdr_balance.configure(text=f"${equity:,.2f} USDT / ≈{btc_eq:.4f} BTC")
        c_p = NEON_GREEN if tot_pnl_d >= 0 else NEON_RED
        self.hdr_pnl.configure(text=f"{tot_pnl_d:+.2f}$ ({tot_pnl_p:+.2f}%)", text_color=c_p)

        self.card_free_cash.configure(text=f"${cash:,.2f} USDT")
        self.card_funds_trade.configure(text=f"${total_holdings:,.2f} USDT")
        self.card_unrealized_pnl.configure(text=f"{tot_pnl_d:+.2f}$ ({tot_pnl_p:+.2f}%)", text_color=c_p)

        # Вывод лога сделок (trade_audit.csv)
        self.txt_activity_log.delete("1.0", "end")
        if not self.audit_raw.strip():
            self.txt_activity_log.insert("end", f"{'ВРЕМЯ':<20} | {'ПАРА':<10} | {'ДЕЙСТВИЕ':<14} | {'ЦЕНА':<10} | {'PnL ($)'}\n")
            self.txt_activity_log.insert("end", "-" * 70 + "\n")
            self.txt_activity_log.insert("end", "Журнал чист. Ожидание сигналов и первых сделок...\n")
        else:
            rdr = csv.reader(io.StringIO(self.audit_raw))
            for i, r in enumerate(rdr):
                if not r or len(r) < 5:
                    continue
                if i == 0:
                    self.txt_activity_log.insert("end", f"{r[0]:<20} | {r:<10} | {r:<14} | {r[3]:<10} | {r[4]:<10}\n")
                    self.txt_activity_log.insert("end", "=" * 70 + "\n")
                else:
                    try:
                        price_val = f"${float(r[3]):<9.2f}"
                    except Exception:
                        price_val = f"{r[3]:<10}"
                    self.txt_activity_log.insert("end", f"{r[0]:<20} | {r:<10} | {r:<14} | {price_val} | {r[4]:<10}\n")


if __name__ == "__main__":
    app = TradingTerminalPro()
    app.mainloop()