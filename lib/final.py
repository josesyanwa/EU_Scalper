# Ryuryu's FOREX MT5 EURUSD Bot
# * Only Shorts * (Production Mode #6973)
# -------------------------------------
# (c) 2023 Ryan Hayabusa 
# GitHub: https://github.com/ryu878
# Web: https://aadresearch.xyz
# Discord: https://discord.gg/zSw58e9Uvf
# Telegram: https://t.me/aadresearch
# -------------------------------------

import MetaTrader5 as mt5
import pandas as pd
import time
import ta
import schedule
import os
import json
import pytz
from datetime import datetime, time as dtime
from dotenv import load_dotenv
import logging

# Ensure log directory exists
log_dir = "../Lib/logs"
os.makedirs(log_dir, exist_ok=True)

# Configure logging
logging.basicConfig(
    filename=os.path.join(log_dir, "mt5_EU_scalper.log"),
    level=logging.INFO,
    format="%(asctime)s - %(message)s"
)

# Load environment variables
load_dotenv()

# Retrieve login credentials and settings from .env
login = int(os.getenv('MT5_LOGIN'))
server = os.getenv('MT5_SERVER')
password = os.getenv('MT5_PASSWORD')
path = os.getenv('MT5_PATH')
timezone = pytz.timezone(os.getenv('TIMEZONE', 'Africa/Nairobi'))
daily_loss_limit = float(os.getenv('DAILY_LOSS_LIMIT', '-200.0'))
daily_drawdown_limit = float(os.getenv('DAILY_DRAWDOWN_LIMIT', '-70.0'))
trading_start_time = datetime.strptime(os.getenv('TRADING_START_TIME', '00:00'), '%H:%M').time()
trading_end_time = datetime.strptime(os.getenv('TRADING_END_TIME', '23:59'), '%H:%M').time()
timeframe = getattr(mt5, os.getenv('TIMEFRAME', 'TIMEFRAME_M1'))
num_candles = int(os.getenv('NUM_CANDLES', '240'))

# Initialize MT5 connection
def initialize_mt5():
    if not mt5.initialize(path=path, login=login, server=server, password=password, timeout=30000):
        print(f"Initialization failed: {mt5.last_error()}")
        logging.error(f"Initialization failed: {mt5.last_error()}")
        mt5.shutdown()
        return False
    print(f"Connected to Account: {mt5.account_info().name}")
    logging.info(f"Connected to Account: {mt5.account_info().name}")
    return True

# Function to calculate daily P/L
def calculate_daily_pl(timezone):
    today = datetime.now(timezone).date()
    start_of_day = datetime.combine(today, dtime(0, 0), tzinfo=timezone)
    end_of_day = datetime.combine(today, dtime(23, 59, 59), tzinfo=timezone)
    
    # Convert to UTC for MT5
    utc_start = start_of_day.astimezone(pytz.utc)
    utc_end = end_of_day.astimezone(pytz.utc)
    
    # Get closed trades from history
    history = mt5.history_deals_get(utc_start, utc_end)
    if history is None or len(history) == 0:
        return 0.0  # No trades today
    
    total_pl = 0.0
    for deal in history:
        if deal.type in (mt5.DEAL_TYPE_BUY, mt5.DEAL_TYPE_SELL):
            total_pl += deal.profit  # Sum profit/loss for each deal
    
    return total_pl

# Function to save drawdown state to JSON
def save_drawdown_state(max_pl, date, filename="../json/drawdown_state.json"):
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    with open(filename, 'w') as f:
        json.dump({'max_daily_pl': max_pl, 'last_date': date.strftime('%Y-%m-%d')}, f)

# Function to load drawdown state from JSON
def load_drawdown_state(filename="../json/drawdown_state.json"):
    try:
        with open(filename, 'r') as f:
            data = json.load(f)
            return {
                'max_daily_pl': data.get('max_daily_pl', 0.0),
                'last_date': datetime.strptime(data.get('last_date', '1970-01-01'), '%Y-%m-%d').date()
            }
    except (FileNotFoundError, json.JSONDecodeError):
        return {'max_daily_pl': 0.0, 'last_date': None}

# Function to check daily drawdown limit
def check_daily_drawdown(timezone, drawdown_limit):
    current_pl = calculate_daily_pl(timezone)
    
    # Load or initialize drawdown state
    state = load_drawdown_state()
    max_daily_pl = state['max_daily_pl']
    last_date = state['last_date']
    
    # Initialize or update max_daily_pl for the day
    today = datetime.now(timezone).date()
    if last_date != today:
        max_daily_pl = current_pl
    else:
        max_daily_pl = max(max_daily_pl, current_pl)
    
    # Save updated state only if changed
    if max_daily_pl != state['max_daily_pl'] or last_date != today:
        save_drawdown_state(max_daily_pl, today)
    
    # Check if current P/L has dropped by drawdown_limit from max_daily_pl
    if current_pl <= max_daily_pl + drawdown_limit and max_daily_pl > 0:
        message = f"🚫 DAILY DRAWDOWN HIT: P/L dropped from ${max_daily_pl:.2f} to ${current_pl:.2f}. Trading paused for today."
        print(message)
        logging.info(message)
        return False
    return True

# Function to check if current time is within the allowed time range
def is_within_time_ranges(time_ranges, timezone):
    now = datetime.now(timezone).time()
    for start_time, end_time in time_ranges:
        if start_time <= end_time:
            if start_time <= now <= end_time:
                return True
        else:
            if now >= start_time or now <= end_time:
                return True
    return False

# Main settings
magic = 12345678
account_id = 1234567890

# Symbol settings
symbol = 'EURUSD'
sl_multiplier = 13
lot = 0.1
add_lot = 0.01
min_deleverage = 15
deleverage_steps = 7
take_profit_short = 21
sl_short = take_profit_short * sl_multiplier

# Get bars and calculate SMA
def get_sma():
    bars = mt5.copy_rates_from_pos(symbol, timeframe, 0, num_candles)
    if bars is None:
        print(f'copy_rates_from_pos() failed, error code = {mt5.last_error()}')
        logging.error(f'copy_rates_from_pos() failed, error code = {mt5.last_error()}')
        return False

    df = pd.DataFrame(bars)
    df.set_index(pd.to_datetime(df['time'], unit='s'), inplace=True)
    df.drop(columns=['time'], inplace=True)
    df['sma_6H'] = ta.trend.sma_indicator(df['high'], window=6)
    df['sma_6L'] = ta.trend.sma_indicator(df['low'], window=6)
    df['sma_33'] = ta.trend.sma_indicator(df['close'], window=33)
    df['sma_60'] = ta.trend.sma_indicator(df['close'], window=60)
    df['sma_120'] = ta.trend.sma_indicator(df['close'], window=120)
    df['sma_240'] = ta.trend.sma_indicator(df['close'], window=240)

    global sma6H, sma6L, sma33, sma60, sma120, sma240
    sma6H = df['sma_6H'].iloc[-1]
    sma6L = df['sma_6L'].iloc[-1]
    sma33 = df['sma_33'].iloc[-1]
    sma60 = df['sma_60'].iloc[-1]
    sma120 = df['sma_120'].iloc[-1]
    sma240 = df['sma_240'].iloc[-1]
    return True

def get_position_data():
    positions = mt5.positions_get(symbol=symbol)
    if positions is None:
        print(f'No positions on {symbol}')
        logging.info(f'No positions on {symbol}')
    elif len(positions) > 0:
        for position in positions:
            post_dict = position._asdict()
            global pos_price, identifier, volume
            pos_price = post_dict['price_open']
            identifier = post_dict['identifier']
            volume = post_dict['volume']
            print(pos_price, identifier, volume)
            logging.info(f"Position: {symbol}, Price: {pos_price}, ID: {identifier}, Volume: {volume}")

# Define prices
def get_ask_bid():
    global ask, bid
    ask = mt5.symbol_info_tick(symbol).ask
    bid = mt5.symbol_info_tick(symbol).bid

def run_trading_script():
    # Check MT5 connection
    if not mt5.terminal_info():
        print("MT5 connection lost, attempting to reconnect...")
        logging.info("MT5 connection lost, attempting to reconnect...")
        if not initialize_mt5():
            print("Reconnection failed, skipping trading logic.")
            logging.error("Reconnection failed, skipping trading logic.")
            return

    # Define trading time range
    trading_time_ranges = [(trading_start_time, trading_end_time)]
    
    # Check if current time is within allowed trading range
    current_time = datetime.now(timezone).strftime("%H:%M:%S")
    if not is_within_time_ranges(trading_time_ranges, timezone):
        message = f"🚫 NOT TRADING TIME: {current_time} is outside allowed trading hours ({trading_start_time.strftime('%H:%M')}–{trading_end_time.strftime('%H:%M')})."
        print(message)
        logging.info(message)
        return  # Skip trading logic

    # Check daily loss limit
    daily_pl = calculate_daily_pl(timezone)
    if daily_pl <= daily_loss_limit:
        message = f"🚫 DAILY LOSS LIMIT HIT: ${-daily_pl:.2f} exceeds ${-daily_loss_limit:.2f}. Trading paused for today."
        print(message)
        logging.info(message)
        return  # Skip trading logic

    # Check daily drawdown limit
    if not check_daily_drawdown(timezone, daily_drawdown_limit):
        return  # Skip trading logic

    # Log current P/L status
    message = f"Daily P/L: ${daily_pl:.2f}"
    print(message)
    logging.info(message)

    # Ensure symbol is selected
    if not mt5.symbol_select(symbol):
        print(f'symbol_select({symbol}) failed, error code = {mt5.last_error()}')
        logging.error(f'symbol_select({symbol}) failed, error code = {mt5.last_error()}')
        return

    # Get point and deviation
    point = mt5.symbol_info(symbol).point
    deviation = 20

    # Reset global variables
    global identifier, volume, pos_price
    identifier = 0
    volume = 0
    pos_price = 0

    # Fetch data
    if not get_sma():
        return
    get_ask_bid()
    get_position_data()

    # Define Sell Order
    sell_order = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lot,
        "type": mt5.ORDER_TYPE_SELL,
        "price": ask,
        "sl": ask + sl_short * point,
        "tp": ask - take_profit_short * point,
        "deviation": deviation,
        "magic": magic,
        "comment": "python short",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    additional_sell_order = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": add_lot,
        "type": mt5.ORDER_TYPE_SELL,
        "price": ask,
        "sl": pos_price + sl_short * point,
        "tp": pos_price - take_profit_short * point,
        "deviation": deviation,
        "magic": magic,
        "comment": "python short",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    sltp_request_sell_pos = {
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": symbol,
        "volume": float(volume),
        "type": mt5.ORDER_TYPE_SELL,
        "position": identifier,
        "sl": pos_price + sl_short * point,
        "tp": pos_price - take_profit_short * point,
        "magic": magic,
        "comment": "Change stop loss for Sell position",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    sltp_request_buy_pos = {
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": symbol,
        "volume": float(volume),
        "type": mt5.ORDER_TYPE_BUY,
        "position": identifier,
        "sl": pos_price - sl_short * point,
        "tp": pos_price + take_profit_short * point,
        "magic": magic,
        "comment": "Change stop loss for Buy position",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    # Check if MA order is OK
    good_long_ma_order = ask > sma6H

    # First Entry
    if pos_price == 0 and good_long_ma_order:
        sell = mt5.order_send(sell_order)
        if sell and sell.retcode == mt5.TRADE_RETCODE_DONE:
            print(f"Initial sell order placed for {symbol}")
            logging.info(f"Initial sell order placed for {symbol}")
        else:
            print(f"Initial sell order failed for {symbol}, retcode={sell.retcode if sell else 'None'}")
            logging.error(f"Initial sell order failed for {symbol}, retcode={sell.retcode if sell else 'None'}")
    else:
        print(f'{symbol} Not Ready')
        logging.info(f'{symbol} Not Ready')

    # Additional Entry
    if pos_price > 0 and good_long_ma_order and sma6L > pos_price:
        sell = mt5.order_send(additional_sell_order)
        if sell and sell.retcode == mt5.TRADE_RETCODE_DONE:
            print(f"Additional sell order placed for {symbol}")
            logging.info(f"Additional sell order placed for {symbol}")
            time.sleep(0.01)
            check_sl = mt5.order_send(sltp_request_sell_pos)
            if check_sl and check_sl.retcode == mt5.TRADE_RETCODE_DONE:
                print(f"SL/TP updated for {symbol} position {identifier}")
                logging.info(f"SL/TP updated for {symbol} position {identifier}")
            else:
                print(f"SL/TP update failed for {symbol}, retcode={check_sl.retcode if check_sl else 'None'}")
                logging.error(f"SL/TP update failed for {symbol}, retcode={check_sl.retcode if check_sl else 'None'}")
        else:
            print(f"Additional sell order failed for {symbol}, retcode={sell.retcode if sell else 'None'}")
            logging.error(f"Additional sell order failed for {symbol}, retcode={sell.retcode if sell else 'None'}")

# Main script
if __name__ == "__main__":
    print("Starting EU Scalper...")
    logging.info("Starting EU Scalper...")
    if not initialize_mt5():
        print("Exiting due to initialization failure.")
        logging.error("Exiting due to initialization failure.")
        exit()

    # Schedule the script to run every minute at :53
    schedule.every().minute.at(":53").do(run_trading_script)

    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        print("Script interrupted by user.")
        logging.info("Script interrupted by user.")
    finally:
        mt5.shutdown()
        print("MT5 connection closed.")
        logging.info("MT5 connection closed.")