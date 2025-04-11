import time
from datetime import datetime, timedelta
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from io import BytesIO
from binance.client import Client
from talib import RSI, EMA, BBANDS
import telegram
from telegram.ext import Updater, CommandHandler
import logging

# ===== Конфигурация =====
API_KEY = cNKbtsNBQyeGS7EWqTHzfWR6tWvVMtht4xsJ9OR3zoGQvUg24xggsLHTNsi8lRaq
API_SECRET = 9gpbVro8QZLuBy8T1YYv1OQENwnbiPeFisBg9mkikyLbcYfeDZGrxOWYBuhoDERM
TELEGRAM_TOKEN = 7650263177:AAHM0dyJ5gGHISiqoyrtc9lmXuCKmF6XHxE
TELEGRAM_CHAT_ID = 160247799


PAIRS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]
TIMEFRAME = Client.KLINE_INTERVAL_5MINUTE
MAX_MEANINGFUL_TRADES = 20
RISK_PER_TRADE = 0.0005
STOP_LOSS_PCT = 0.005
TAKE_PROFIT_PCT = 0.01

# ===== Инициализация =====
client = Client(API_KEY, API_SECRET)
bot = telegram.Bot(token=TELEGRAM_TOKEN)
logging.basicConfig(filename='ai_scalping_bot.log', level=logging.INFO)

class TradingJournal:
    def __init__(self):
        self.trades = pd.DataFrame(columns=[
            'pair', 'strategy', 'direction', 'entry_price',
            'exit_price', 'pnl', 'pnl_pct', 'duration', 'date', 'status'
        ])
        self.load_history()
    
    def load_history(self):
        try:
            self.trades = pd.read_csv('trading_journal.csv', parse_dates=['date'])
        except FileNotFoundError:
            pass
    
    def add_trade(self, trade_data):
        if abs(trade_data['pnl_pct']) >= 0.01:
            self.trades = pd.concat([self.trades, pd.DataFrame([trade_data])], ignore_index=True)
            self.trades.to_csv('trading_journal.csv', index=False)
            return True
        return False
    
    def generate_report(self, period='day'):
        now = datetime.now()
        
        if period == 'day':
            filtered = self.trades[self.trades['date'] >= now - timedelta(days=1)]
        elif period == 'week':
            filtered = self.trades[self.trades['date'] >= now - timedelta(weeks=1)]
        elif period == 'month':
            filtered = self.trades[self.trades['date'] >= now - timedelta(days=30)]
        else:
            filtered = self.trades
        
        if filtered.empty:
            return None, "Нет данных за выбранный период"
        
        stats = {
            'total_trades': len(filtered),
            'profitable': len(filtered[filtered['pnl'] > 0]),
            'total_pnl': filtered['pnl'].sum(),
            'win_rate': (len(filtered[filtered['pnl'] > 0]) / len(filtered)) * 100,
            'best_trade': filtered['pnl'].max(),
            'worst_trade': filtered['pnl'].min(),
            'avg_duration': filtered['duration'].mean()
        }
        
        plt.figure(figsize=(10, 5))
        filtered['cum_pnl'] = filtered['pnl'].cumsum()
        plt.plot(filtered['date'], filtered['cum_pnl'], marker='o')
        plt.title(f"Кумулятивная прибыль ({period})")
        plt.xlabel("Время")
        plt.ylabel("USDT")
        
        buf = BytesIO()
        plt.savefig(buf, format='png', bbox_inches='tight')
        buf.seek(0)
        plt.close()
        
        report_text = (
            f"📊 Отчет за {period}\n"
            f"• Сделок: {stats['total_trades']}\n"
            f"• Прибыльных: {stats['profitable']} ({stats['win_rate']:.1f}%)\n"
            f"• Общий PnL: {stats['total_pnl']:.2f} USDT\n"
            f"• Лучшая сделка: +{stats['best_trade']:.2f} USDT\n"
            f"• Худшая сделка: {stats['worst_trade']:.2f} USDT\n"
            f"• Среднее время: {stats['avg_duration']:.1f} мин"
        )
        
        return buf, report_text

journal = TradingJournal()

def send_telegram_alert(message):
    try:
        bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
    except Exception as e:
        logging.error(f"Telegram error: {e}")

def get_current_price(pair):
    return float(client.get_symbol_ticker(symbol=pair)['price'])

def calculate_position_size(balance):
    max_loss = balance * RISK_PER_TRADE
    return min(max_loss / STOP_LOSS_PCT, balance * 0.1)

def execute_trade(pair, strategy_num):
    prices = get_candles(pair, TIMEFRAME)
    if len(prices) < 20:
        return None
    
    indicators = {
        'ema_fast': EMA(prices, timeperiod=9)[-1],
        'ema_slow': EMA(prices, timeperiod=21)[-1],
        'rsi': RSI(prices, timeperiod=14)[-1],
        'upper_bb': BBANDS(prices, timeperiod=20)[0][-1],
        'lower_bb': BBANDS(prices, timeperiod=20)[2][-1]
    }
    
    signal = None
    price = prices[-1]
    
    if strategy_num == 1:
        if indicators['ema_fast'] > indicators['ema_slow'] and indicators['rsi'] < 30:
            signal = "BUY"
        elif indicators['ema_fast'] < indicators['ema_slow'] and indicators['rsi'] > 70:
            signal = "SELL"
    
    elif strategy_num == 2:
        if price <= indicators['lower_bb'] and indicators['rsi'] < 35:
            signal = "BUY"
        elif price >= indicators['upper_bb'] and indicators['rsi'] > 65:
            signal = "SELL"
    
    if signal:
        amount = calculate_position_size(get_balance()) / price
        order = client.create_order(
            symbol=pair,
            side=signal,
            type=Client.ORDER_TYPE_LIMIT,
            timeInForce=Client.TIME_IN_FORCE_GTC,
            quantity=round(amount, 4),
            price=str(price)
        )
        return order
    return None

def monitor_trade_execution(pair, order):
    start_time = time.time()
    entry_price = float(order['price'])
    direction = order['side']
    
    while (time.time() - start_time) < 300:
        try:
            order_status = client.get_order(
                symbol=pair,
                orderId=order['orderId']
            )
            
            if order_status['status'] == 'FILLED':
                exit_price = float(order_status['avgPrice'])
                duration = (time.time() - start_time) / 60
                pnl = exit_price - entry_price if direction == "BUY" else entry_price - exit_price
                
                trade_data = {
                    'pair': pair,
                    'strategy': ['EMA+RSI', 'Bollinger', 'OrderBook'][strategy_num-1],
                    'direction': direction,
                    'entry_price': entry_price,
                    'exit_price': exit_price,
                    'pnl': pnl,
                    'pnl_pct': (pnl / entry_price) * 100,
                    'duration': duration,
                    'date': datetime.now(),
                    'status': 'TP/SL' if abs(pnl) > 0 else 'Time'
                }
                
                if journal.add_trade(trade_data):
                    send_telegram_alert(
                        f"🎯 Сделка #{len(journal.trades)}\n"
                        f"Пара: {pair}\n"
                        f"Стратегия: {trade_data['strategy']}\n"
                        f"Направление: {direction}\n"
                        f"Цена: {entry_price:.2f} → {exit_price:.2f}\n"
                        f"PnL: {trade_data['pnl_pct']:.2f}%\n"
                        f"Длительность: {duration:.1f} мин"
                    )
                return True
        
        except Exception as e:
            logging.error(f"Order check error: {e}")
        
        time.sleep(10)
    
    client.cancel_order(symbol=pair, orderId=order['orderId'])
    exit_price = get_current_price(pair)
    pnl = exit_price - entry_price if direction == "BUY" else entry_price - exit_price
    
    trade_data = {
        'pair': pair,
        'strategy': ['EMA+RSI', 'Bollinger', 'OrderBook'][strategy_num-1],
        'direction': direction,
        'entry_price': entry_price,
        'exit_price': exit_price,
        'pnl': pnl,
        'pnl_pct': (pnl / entry_price) * 100,
        'duration': 5.0,
        'date': datetime.now(),
        'status': 'Expired'
    }
    
    journal.add_trade(trade_data)
    return False

def handle_report(update, context):
    period = context.args[0] if context.args else 'day'
    chart, text = journal.generate_report(period)
    
    if chart:
        context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=chart,
            caption=text
        )
    else:
        context.bot.send_message(
            chat_id=update.effective_chat.id,text=text
        )

def main():
    updater = Updater(TELEGRAM_TOKEN)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("report", handle_report))
    updater.start_polling()
    
    send_telegram_alert("🤖 Торговый бот запущен!")
    
    while True:
        try:
            current_day = datetime.now().strftime("%Y-%m-%d")
            meaningful_trades_today = len(journal.trades[journal.trades['date'].dt.strftime('%Y-%m-%d') == current_day])
            
            if meaningful_trades_today >= MAX_MEANINGFUL_TRADES:
                time.sleep(60)
                continue
            
            for pair in PAIRS:
                strategy_num = np.random.randint(1, 4)
                order = execute_trade(pair, strategy_num)
                
                if order:
                    monitor_trade_execution(pair, order)
            
            time.sleep(60)
        
        except Exception as e:
            send_telegram_alert(f"⚠️ Критическая ошибка: {str(e)}")
            time.sleep(60)

if __name__ == "__main__":
    main()
