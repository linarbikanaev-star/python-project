#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Живой торговый симулятор (Live Runner) с институциональной математикой v2.1.
Ключевые отличия от v1:
1. Вход: истинный пробой 20-периодного канала Дончяна (Close > Donchian High 20), а не EMA50.
2. Неискаженный объем: SMA(20) объема считается со сдвигом на 1 бар назад (shift 1).
3. Дневной тренд: фильтр EMA(200) на 1D проверяется по ПОСЛЕДНЕЙ ЗАКРЫТОЙ дневной свече (без мерцания внутри дня).
4. Трейлинг-выход: удержание остатка позиции по Donchian Low (20 свечей) после фиксации 50% на 2R (+2x ATR) и безубытка.
5. Telegram: 4-часовой подробный срез рынка, 24-часовой консольный аудит, интерактив по команде /balance, авто-фолбэк разметки.
"""

import os, sys, time, json, signal, threading, urllib.request, urllib.parse
from datetime import datetime
import ccxt, pandas as pd, numpy as np

# ==========================================
# КОНФИГУРАЦИЯ TELEGRAM И СЕТИ
# ==========================================
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "8671375410:AAHuL2IQC6hvAEG1ZX8fCnmGb7CAP8c0BHU")
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "6248193776")

def send_telegram(msg: str, parse_mode="HTML"):
    """Надежная отправка сообщений в Telegram с защитным таймаутом и авто-фолбэком."""
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    for mode in [parse_mode, None]:
        try:
            p = {"chat_id": TG_CHAT_ID, "text": msg}
            if mode: p["parse_mode"] = mode
            d = urllib.parse.urlencode(p).encode("utf-8")
            req = urllib.request.Request(url, data=d)
            with urllib.request.urlopen(req, timeout=8) as r:
                return True
        except Exception as e:
            if mode:
                for t in ["<b>", "</b>", "<i>", "</i>", "<code>", "</code>", "<pre>", "</pre>"]:
                    msg = msg.replace(t, "")
    return False

# ==========================================
# ПАРАМЕТРЫ СТРАТЕГИИ V2 (ИНСТИТУЦИОНАЛЬНЫЙ ДОНЧЯН)
# ==========================================
exchange = ccxt.bybit({"enableRateLimit": True, "timeout": 15000})

PAIRS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT"]
TIMEFRAME = "1h"          # Рабочий таймфрейм 1 час
HTF_TIMEFRAME = "1d"      # Старший дневной тренд EMA(200)
CANDLE_LIMIT = 250        # Выборка свечей

INITIAL_CASH = 1000.0     # Баланс $1000
RISK_PER_TRADE = 0.01     # 1% риска от капитала на сделку
MAX_OPEN_POSITIONS = 2    # Максимум 2 одновременные позиции
SLIPPAGE = 0.0005         # Проскальзывание 0.05%
COMMISSION = 0.001        # Комиссия Taker 0.1%

ATR_PERIOD = 14
ATR_MULTIPLIER = 2.0      # Стоп = 2.0 * ATR(14)
VOL_SMA_PERIOD = 20
VOL_MULTIPLIER = 1.5      # Импульс объема > 1.5 * SMA(20)
DONCHIAN_PERIOD = 20      # Канал Дончяна 20 периодов (Turtle Breakout)
TAKE_PROFIT_R = 2.0       # Тейк 50% на дистанции 2R (+2 * ATR Стопа)

REPORT_INTERVAL = 14400   # Регулярный отчет каждые 4 часа
DAILY_INTERVAL = 86400    # Суточный консольный аудит раз в 24 часа

STATE_FILE = "bot_state.json"
AUDIT_FILE = "trade_audit.csv"


class MultiAssetSimulator:
    def __init__(self, initial_cash=1000.0):
        self.initial_balance = self.cash = initial_cash
        self.positions = {}
        self.is_running = True
        self.start_time = datetime.now()
        self.last_report_ts = self.last_tg_id = 0
        self.last_daily_ts = time.time()

        self.init_audit_file()
        self.load_state()

        # Системные сигналы остановки
        signal.signal(signal.SIGINT, self.handle_exit)
        signal.signal(signal.SIGTERM, self.handle_exit)
        if hasattr(signal, "SIGHUP"): signal.signal(signal.SIGHUP, self.handle_exit)

        self.save_state(status="RUNNING")
        st_msg = (
            "🚀 <b>Торговый симулятор ЗАПУЩЕН [ЯДРО V2.1]</b>\n"
            f"💰 Стартовый капитал: <b>${self.cash:,.2f} USDT</b>\n"
            "📊 Мониторинг пар: <code>BTC, ETH, SOL, XRP</code>\n"
            "⚙️ Модель V2.1: Пробой Дончяна (20) | 1D EMA(200) Closed | Объем 1.5x несмещенный | Стоп 2xATR\n"
            "🕒 Регулярный отчет: <b>каждые 4 часа</b>\n"
            "💬 Команды: отправьте <code>/balance</code> или <code>баланс</code> в чат"
        )
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] СИМУЛЯТОР V2.1 ЗАПУЩЕН | Кэш: ${self.cash:,.2f}")
        send_telegram(st_msg)

        # Первый отчет сразу при старте
        self.send_periodic_market_report(force=True)

        # Фоновый слушатель команд из Telegram
        threading.Thread(target=self._tg_command_loop, daemon=True).start()

    def _tg_command_loop(self):
        """Интерактивный опрос команд пользователя из Telegram."""
        time.sleep(3)
        while self.is_running:
            try:
                url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/getUpdates?offset={self.last_tg_id + 1}&timeout=10"
                with urllib.request.urlopen(urllib.request.Request(url), timeout=12) as resp:
                    data = json.loads(resp.read().decode())
                    if data.get("ok"):
                        for u in data.get("result", []):
                            self.last_tg_id = u["update_id"]
                            txt = (u.get("message", {}).get("text") or "").strip().lower()
                            cid = str(u.get("message", {}).get("chat", {}).get("id", ""))
                            if cid == str(TG_CHAT_ID):
                                if txt in ["/balance", "/status", "/report", "баланс", "отчет", "статус"]:
                                    self.send_periodic_market_report(force=True, is_manual=True)
                                elif txt in ["/help", "помощь"]:
                                    help_text = (
                                        "🤖 <b>Команды бота:</b>\n"
                                        "• <code>/balance</code> или <code>баланс</code> — актуальный срез рынка, вычисления и слоты\n"
                                        "• <code>/status</code> — статус работы\n"
                                        "Автоматические отчеты приходят каждые 4 часа."
                                    )
                                    send_telegram(help_text)
            except Exception: pass
            time.sleep(2)

    def handle_exit(self, signum=None, frame=None):
        if not self.is_running: return
        self.is_running = False
        self.save_state(status="STOPPED")
        up = str(datetime.now() - self.start_time).split('.')[0]
        sp_msg = (
            "🛑 <b>Торговый симулятор ОСТАНОВЛЕН!</b>\n"
            f"💵 Итоговый кэш: <b>${self.cash:,.2f} USDT</b>\n"
            f"📦 Открытых позиций: <b>{len(self.positions)}</b>\n"
            f"⏳ Время работы: <b>{up}</b>"
        )
        print("\n" + sp_msg.replace("<b>", "").replace("</b>", ""))
        send_telegram(sp_msg)
        sys.exit(0)

    def init_audit_file(self):
        if not os.path.exists(AUDIT_FILE):
            with open(AUDIT_FILE, "w", encoding="utf-8") as f:
                f.write("timestamp,symbol,action,price,pnl_dollar,pnl_percent,fee,cash_after\n")

    def load_state(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    d = json.load(f)
                    self.cash = float(d.get("cash", self.cash))
                    self.initial_balance = float(d.get("initial_balance", self.initial_balance))
                    self.positions = d.get("positions", {})
                print(f"[STATE] Баланс восстановлен: ${self.cash:,.2f} | Позиций: {len(self.positions)}")
            except Exception as e: print(f"[STATE ERROR]: {e}")

    def save_state(self, status="RUNNING", current_prices=None):
        prices = current_prices or {}
        eq = self.cash + sum(p["amount"] * prices.get(s.replace("/",""), prices.get(s, p["entry_price"])) for s, p in self.positions.items())
        pnl = ((eq - self.initial_balance) / self.initial_balance) * 100
        d = {
            "status": status, "cash": round(self.cash, 2), "equity": round(eq, 2),
            "total_pnl_pct": round(pnl, 2), "initial_balance": round(self.initial_balance, 2),
            "positions": self.positions, "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        with open(STATE_FILE, "w", encoding="utf-8") as f: json.dump(d, f, indent=4)

    def log_audit(self, symbol, action, price, pnl_dollar=0.0, pnl_percent=0.0, fee=0.0):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        row = f"{ts},{symbol},{action},{price:.2f},{pnl_dollar:.2f},{pnl_percent:.2f}%,{fee:.2f},{self.cash:.2f}\n"
        with open(AUDIT_FILE, "a", encoding="utf-8") as f: f.write(row)

    def fetch_data(self, symbol, timeframe, limit=250):
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            if not ohlcv or len(ohlcv) < 50: return None
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            return df
        except Exception as e:
            print(f"[FETCH ERROR] {symbol} ({timeframe}): {e}")
            return None

    @staticmethod
    def calculate_atr(df, period=14):
        h, l, cp = df['high'], df['low'], df['close'].shift(1)
        tr = pd.concat([h - l, (h - cp).abs(), (l - cp).abs()], axis=1).max(axis=1)
        return tr.rolling(window=period).mean()

    def get_market_analysis(self, symbol):
        """Институциональный расчет индикаторов v2 без Lookahead-bias."""
        # 1. Старший тренд 1D: строго по ПОСЛЕДНЕЙ ЗАКРЫТОЙ дневной свече
        df_1d = self.fetch_data(symbol, HTF_TIMEFRAME, limit=CANDLE_LIMIT)
        if df_1d is None or len(df_1d) < 200: return None
        df_1d['ema200'] = df_1d['close'].ewm(span=200, adjust=False).mean()
        # Закрытая дневная свеча — это предпоследняя (iloc[-2]), так как iloc[-1] еще торгуется
        last_closed_1d = df_1d.iloc[-2]
        htf_bullish = last_closed_1d['close'] > last_closed_1d['ema200']
        ema200_val = last_closed_1d['ema200']

        # 2. Рабочий интервал 1H
        df_1h = self.fetch_data(symbol, TIMEFRAME, limit=CANDLE_LIMIT)
        if df_1h is None or len(df_1h) < 55: return None

        df_1h['ema50'] = df_1h['close'].ewm(span=50, adjust=False).mean()
        df_1h['atr'] = self.calculate_atr(df_1h, period=ATR_PERIOD)

        # Несмещенный объем: среднее за 20 свечей, сдвинутое на 1 бар назад
        df_1h['vol_sma_prev'] = df_1h['volume'].shift(1).rolling(window=VOL_SMA_PERIOD).mean()

        # Канал Дончяна (20 свечей) по предыдущим закрытым свечам
        df_1h['donchian_high'] = df_1h['high'].shift(1).rolling(window=DONCHIAN_PERIOD).max()
        df_1h['donchian_low'] = df_1h['low'].shift(1).rolling(window=DONCHIAN_PERIOD).min()

        return {
            'htf_bullish': htf_bullish,
            'ema200': ema200_val,
            'last_candle': df_1h.iloc[-1],
            'prev_candle': df_1h.iloc[-2],
            'df_1h': df_1h
        }

    def get_equity(self, current_prices):
        return self.cash + sum(p["amount"] * current_prices.get(s.replace("/",""), current_prices.get(s, p["entry_price"])) for s, p in self.positions.items())

    def check_entry_signal(self, symbol, analysis):
        """Логика входа V2: истинный пробой верхней границы канала Дончяна 20."""
        if len(self.positions) >= MAX_OPEN_POSITIONS: return
        if symbol in self.positions: return
        if not analysis['htf_bullish']: return

        curr = analysis['last_candle']
        prev = analysis['prev_candle']
        d_high = curr['donchian_high']
        vol_base = curr['vol_sma_prev']

        if pd.isna(d_high) or pd.isna(vol_base) or vol_base <= 0: return

        # Сигнал V2: закрытие текущей свечи строго выше 20-периодного максимума
        breakout = curr['close'] > d_high
        volume_confirmed = curr['volume'] >= (VOL_MULTIPLIER * vol_base)

        if breakout and volume_confirmed:
            self.execute_entry(symbol, curr)

    def execute_entry(self, symbol, candle):
        price = candle['close'] * (1.0 + SLIPPAGE)
        atr = candle['atr']
        if pd.isna(atr) or atr <= 0: return

        stop_distance = ATR_MULTIPLIER * atr
        stop_loss = price - stop_distance

        eq = self.get_equity({symbol: candle['close']})
        risk_cash = eq * RISK_PER_TRADE
        amount = risk_cash / stop_distance
        notional = amount * price

        if notional > self.cash:
            amount = self.cash / price
            notional = amount * price

        if amount <= 0 or notional < 5.0: return

        fee = notional * COMMISSION
        self.cash -= (notional + fee)

        self.positions[symbol] = {
            "entry_price": round(price, 4), "amount": round(amount, 6),
            "stop_loss": round(stop_loss, 4), "stop_distance": round(stop_distance, 4),
            "take_profit_hit": False, "opened_at": str(candle['timestamp'])
        }

        self.log_audit(symbol=symbol, action="BUY", price=price, fee=fee)
        self.save_state(status="RUNNING")

        tg = (
            f"🟢 <b>[ВХОД LONG ПО ДОНЧЯНУ] {symbol}</b>\n"
            f"Цена: <b>${price:,.2f}</b> | Объём: <code>{amount:.4f}</code> (${notional:,.2f})\n"
            f"Стоп-лосс (2xATR): <b>${stop_loss:,.2f}</b> (-{(stop_distance/price)*100:.2f}%)\n"
            f"Цель ТП-50% (2R): <b>${price + (TAKE_PROFIT_R * stop_distance):,.2f}</b>\n"
            f"Остаток кэша: <b>${self.cash:,.2f} USDT</b>"
        )
        print(f"[{candle['timestamp']}] [ВХОД LONG] {symbol} | Цена: {price:,.2f} | Стоп: {stop_loss:,.2f}")
        send_telegram(tg)

    def manage_open_positions(self, symbol, analysis):
        if symbol not in self.positions: return
        pos = self.positions[symbol]
        c = analysis['last_candle']
        low, high, close = c['low'], c['high'], c['close']

        # 1. Стоп-лосс
        if low <= pos['stop_loss']:
            ep = pos['stop_loss'] * (1.0 - SLIPPAGE)
            net = (ep - pos['entry_price']) * pos['amount'] - (pos['amount'] * ep * COMMISSION)
            pct = ((ep - pos['entry_price']) / pos['entry_price']) * 100
            self.cash += (pos['amount'] * ep) * (1.0 - COMMISSION)
            self.log_audit(symbol=symbol, action="STOP-LOSS", price=ep, pnl_dollar=net, pnl_percent=pct)
            del self.positions[symbol]
            self.save_state(status="RUNNING")
            send_telegram(f"🛑 <b>[СТОП-ЛОСС] {symbol}</b>\nВыход: ${ep:,.2f} | PnL: <b>{net:+,.2f}$ ({pct:+.2f}%)</b>")
            return

        # 2. Частичный тейк-профит 50% на 2R (R:R 1:2)
        tp = pos['entry_price'] + (TAKE_PROFIT_R * pos['stop_distance'])
        if not pos['take_profit_hit'] and high >= tp:
            part = pos['amount'] * 0.5
            ep = tp * (1.0 - SLIPPAGE)
            net = (ep - pos['entry_price']) * part - (part * ep * COMMISSION)
            pct = ((ep - pos['entry_price']) / pos['entry_price']) * 100
            self.cash += (part * ep) * (1.0 - COMMISSION)
            pos['amount'] -= part
            pos['take_profit_hit'] = True
            pos['stop_loss'] = pos['entry_price'] # Перенос в безубыток
            self.log_audit(symbol=symbol, action="TAKE-PROFIT 50%", price=ep, pnl_dollar=net, pnl_percent=pct)
            self.save_state(status="RUNNING")
            send_telegram(f"🎯 <b>[ТЕЙК-ПРОФИТ 50% (2R)] {symbol}</b>\nВыход: ${ep:,.2f} | Зафиксировано: <b>{net:+,.2f}$</b>\nСтоп перенесен в БЕЗУБЫТОК: ${pos['stop_loss']:,.2f}")

        # 3. Выход V2: закрытие 1H свечи ниже 20-периодного Donchian Low
        d_low = c['donchian_low']
        if not pd.isna(d_low) and close < d_low:
            ep = close * (1.0 - SLIPPAGE)
            net = (ep - pos['entry_price']) * pos['amount'] - (pos['amount'] * ep * COMMISSION)
            pct = ((ep - pos['entry_price']) / pos['entry_price']) * 100
            self.cash += (pos['amount'] * ep) * (1.0 - COMMISSION)
            self.log_audit(symbol=symbol, action="EXIT (Donchian 20)", price=ep, pnl_dollar=net, pnl_percent=pct)
            del self.positions[symbol]
            self.save_state(status="RUNNING")
            send_telegram(f"🏁 <b>[ТРЕЙЛИНГ-ВЫХОД DONCHIAN 20] {symbol}</b>\nВыход: ${ep:,.2f} | PnL: <b>{net:+,.2f}$ ({pct:+.2f}%)</b>")

    def send_periodic_market_report(self, force=False, is_manual=False):
        """Отправка отчета каждые 4 часа: цены рынка, баланс и расчет условий по ВСЕМ 4 парам."""
        now_ts = time.time()
        if not force and (now_ts - self.last_report_ts < REPORT_INTERVAL): return
        self.last_report_ts = now_ts
        dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        prices = {}
        for s in PAIRS:
            try: prices[s] = float(exchange.fetch_ticker(s).get("last", 0.0))
            except Exception: prices[s] = 0.0

        eq = self.cash + sum(p["amount"] * prices.get(s, p["entry_price"]) for s, p in self.positions.items())
        pnl = ((eq - self.initial_balance) / self.initial_balance) * 100
        hdr = "⚡ <b>ОТЧЕТ ПО ЗАПРОСУ [ЯДРО V2.1]</b>" if is_manual else "📊 <b>РЕГУЛЯРНЫЙ ОТЧЕТ [4H | ЯДРО V2]</b>"

        lines = [hdr, f"🕒 <code>{dt}</code>\n", "🌐 <b>ТЕКУЩИЙ РЫНОК (СПОТ):</b>"]
        for s in PAIRS:
            p = prices.get(s, 0.0)
            lines.append(f"• <b>{s}:</b> " + (f"${p:,.4f}" if p < 10 else f"${p:,.2f}"))

        lines.extend([
            f"\n💰 <b>ДЕПОЗИТ:</b> <b>${eq:,.2f} USDT</b> ({pnl:+.2f}%) | Кэш: <b>${self.cash:,.2f}</b>",
            f"📦 <b>СЛОТЫ:</b> <b>{len(self.positions)}/{MAX_OPEN_POSITIONS} занято</b> (свободно: {MAX_OPEN_POSITIONS - len(self.positions)})\n",
            "🔍 <b>АНАЛИЗ И ВЫЧИСЛЕНИЯ V2 (ДОНЧЯН 20 + ОБЪЕМ 1.5x):</b>"
        ])

        for idx, s in enumerate(PAIRS, 1):
            cp = prices.get(s, 0.0)
            in_pos = s in self.positions
            st = "🟢 <i>В портфеле</i>" if in_pos else "⚪ <i>Вне рынка</i>"
            lines.append(f"<b>{idx}. [{s}]</b> ({st})")
            if in_pos:
                p = self.positions[s]
                lines.append(f"• Позиция: вход ${p['entry_price']:,.2f} | PnL: {((cp-p['entry_price'])/p['entry_price'])*100:+.2f}%")
            try:
                b1d = exchange.fetch_ohlcv(s, timeframe=HTF_TIMEFRAME, limit=CANDLE_LIMIT)
                df1d = pd.DataFrame(b1d, columns=["t","o","h","l","c","v"])
                ema200 = df1d["c"].ewm(span=200, adjust=False).mean().iloc[-2] # закрытый день

                b1h = exchange.fetch_ohlcv(s, timeframe=TIMEFRAME, limit=55)
                df1h = pd.DataFrame(b1h, columns=["t","o","h","l","c","v"])
                d_high = df1h["h"].shift(1).rolling(DONCHIAN_PERIOD).max().iloc[-1]
                vsma = df1h["v"].shift(1).rolling(VOL_SMA_PERIOD).mean().iloc[-1]
                vr = (df1h["v"].iloc[-1] / vsma) if vsma > 0 else 0.0
                dist = ((d_high - cp) / cp) * 100 if cp > 0 else 0.0

                c_tr = "🟢" if cp > ema200 else "🔴"
                c_bo = "🟢" if cp >= d_high else "🔴"
                c_vo = "🟢" if vr >= VOL_MULTIPLIER else "🔴"
                lines.append(f"• 1D Тренд (Closed): {c_tr} Close выше EMA200 (${ema200:,.2f})")
                lines.append(f"• 1H Дончян (20): {c_bo} Уровень ${d_high:,.2f} (до пробоя: {dist:+.2f}%)")
                lines.append(f"• 1H Объем: {c_vo} {vr:.2f}x от SMA20 (нужно от {VOL_MULTIPLIER}x)")
                if not in_pos:
                    if cp > ema200 and cp >= d_high and vr >= VOL_MULTIPLIER:
                        lines.append("👉 <i>Статус: ВСЕ УСЛОВИЯ V2 ВЫПОЛНЕНЫ (готов к покупке)</i>\n")
                    else:
                        reasons = []
                        if cp <= ema200: reasons.append("ниже дневной EMA200")
                        if cp < d_high: reasons.append(f"рост на {dist:.2f}% до пробоя")
                        if vr < VOL_MULTIPLIER: reasons.append(f"не хватает объема ({vr:.2f}x из {VOL_MULTIPLIER}x)")
                        lines.append(f"👉 <i>Ждет: {'; '.join(reasons)}</i>\n")
                else:
                    lines.append("👉 <i>Удерживается по каналу Donchian Low 20</i>\n")
            except Exception as e:
                lines.append(f"• Ошибка индикаторов: {e}\n")

        lines.append("🎯 <b>Правило входа V2:</b> Свободный слот + 1D Closed выше EMA200 + 1H Donchian High 20 + Объем от 1.5x")
        send_telegram("\n".join(lines))

    def send_daily_console_summary(self):
        now_ts = time.time()
        if now_ts - self.last_daily_ts < DAILY_INTERVAL: return
        self.last_daily_ts = now_ts
        dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        trades = []
        if os.path.exists(AUDIT_FILE):
            try:
                df = pd.read_csv(AUDIT_FILE)
                if not df.empty:
                    for _, r in df.tail(5).iterrows():
                        trades.append(f"{r['timestamp'][-8:]} | {r['symbol']:<8} | {r['action']:<10} | PnL: {r['pnl_dollar']:+.2f}$")
            except Exception: pass
        tr_str = "\n".join(trades) if trades else "Сделок за 24ч не зафиксировано"
        pos_str = "\n".join([f"{s}: вход ${p['entry_price']:,.2f} | объём {p['amount']}" for s, p in self.positions.items()]) if self.positions else "Все позиции закрыты. 100% USDT кэш."
        msg = f"📋 <b>СУТОЧНЫЙ АУДИТ [24H]</b>\n🕒 <code>{dt}</code>\n💰 Кэш: ${self.cash:,.2f} USDT\n\n<b>Позиции:</b>\n<pre>{pos_str}</pre>\n\n<b>События:</b>\n<pre>{tr_str}</pre>"
        send_telegram(msg)

    def run_iteration(self):
        prices = {}
        for s in PAIRS:
            a = self.get_market_analysis(s)
            if a:
                prices[s] = a['last_candle']['close']
                self.manage_open_positions(s, a)
                self.check_entry_signal(s, a)
        eq = self.get_equity(prices)
        self.save_state(status="RUNNING", current_prices=prices)
        self.send_periodic_market_report()
        self.send_daily_console_summary()
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [V2.1] Баланс: ${self.cash:,.2f} | Equity: ${eq:,.2f} | Позиций: {len(self.positions)}/{MAX_OPEN_POSITIONS}")

if __name__ == "__main__":
    sim = MultiAssetSimulator(initial_cash=INITIAL_CASH)
    try:
        while sim.is_running:
            sim.run_iteration()
            time.sleep(60)
    except KeyboardInterrupt:
        sim.handle_exit()