import streamlit as st
import yfinance as yf
import plotly.graph_objects as go
import pandas as pd
from datetime import datetime
import sqlite3
import contextlib

# 設定頁面與版面
st.set_page_config(page_title="穩定版模擬投資", layout="wide")

# ================= 1. 資料庫安全連線工具 =================
DB_NAME = 'trading_v2.db'

@contextlib.contextmanager
def get_db_conn():
    # 加入 timeout 避免多個操作同時擠壓造成崩潰
    conn = sqlite3.connect(DB_NAME, timeout=30, check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    with get_db_conn() as conn:
        c = conn.cursor()
        c.execute('CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, password TEXT, cash REAL)')
        c.execute('CREATE TABLE IF NOT EXISTS holdings (username TEXT, symbol TEXT, shares REAL, avg_cost REAL, PRIMARY KEY(username, symbol))')
        c.execute('CREATE TABLE IF NOT EXISTS trade_history (username TEXT, time TEXT, symbol TEXT, action TEXT, price REAL, quantity REAL, pnl REAL)')
        conn.commit()

# ================= 2. 數據抓取 (防斷線版) =================
@st.cache_data(ttl=300) # 提高快取時間到 5 分鐘，減輕 API 負擔
def fetch_stock_data(stock_code):
    try:
        # 只抓近 60 天，減少數據量
        data = yf.download(stock_code, period="60d", auto_adjust=True, progress=False)
        if data.empty: return None
        if isinstance(data.columns, pd.MultiIndex): data.columns = data.columns.droplevel(1)
        return data
    except:
        return None

def get_user_data(username):
    with get_db_conn() as conn:
        res = conn.execute("SELECT cash FROM users WHERE username=?", (username,)).fetchone()
        cash = res[0] if res else 0
        holdings_df = pd.read_sql(f"SELECT * FROM holdings WHERE username='{username}'", conn)
        history_df = pd.read_sql(f"SELECT * FROM trade_history WHERE username='{username}'", conn)
    return cash, holdings_df, history_df

# ================= 3. 登入系統 =================
def login_system():
    if "user" not in st.session_state: st.session_state.user = None
    if st.session_state.user is None:
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            st.title("💼 模擬投資系統")
            choice = st.segmented_control("操作類型", ["登入", "註冊帳號"], default="登入")
            
            u = st.text_input("帳號")
            p = st.text_input("密碼", type="password")
            
            if choice == "登入":
                if st.button("進入交易中心", use_container_width=True, type="primary"):
                    with get_db_conn() as conn:
                        data = conn.execute("SELECT * FROM users WHERE username=? AND password=?", (u, p)).fetchone()
                    if data:
                        st.session_state.user = u
                        st.rerun()
                    else: st.error("帳號或密碼錯誤")
            else:
                if st.button("確認註冊", use_container_width=True):
                    try:
                        with get_db_conn() as conn:
                            conn.execute("INSERT INTO users VALUES (?, ?, ?)", (u, p, 3000000.0))
                            conn.commit()
                        st.success("註冊成功！請直接登入。")
                    except: st.error("帳號已存在")
        return False
    return True

# ================= 4. 主介面 =================
init_db()

if login_system():
    user = st.session_state.user
    user_cash, df_h, df_t = get_user_data(user)
    
    # 側邊欄資產顯示
    with st.sidebar:
        st.subheader(f"👤 {user}")
        if st.button("安全登出"):
            st.session_state.user = None
            st.rerun()
        st.divider()
        st.metric("剩餘現金", f"${user_cash:,.0f}")

    # 主畫面布局
    st.title("📈 交易面板")
    
    stock_input = st.text_input("輸入股票代碼", "").upper()
    data = fetch_stock_data(stock_input)

    if data is not None:
        price = float(data["Close"].iloc[-1])
        change = price - float(data["Close"].iloc[-2])
        
        c1, c2 = st.columns([3, 2])
        with c1:
            fig = go.Figure(data=[go.Candlestick(
                x=data.index, open=data['Open'], high=data['High'], low=data['Low'], close=data['Close'],
                increasing_line_color='red', decreasing_line_color='green'
            )])
            fig.update_layout(height=400, template="plotly_dark", margin=dict(l=0,r=0,b=0,t=0))
            st.plotly_chart(fig, use_container_width=True)
        
        with c2:
            st.metric("最新成交價", f"{price:.2f}", f"{change:+.2f}")
            
            with st.container(border=True):
                mode = st.radio("交易指令", ["買入", "賣出"], horizontal=True)
                lots = st.number_input("數量 (張)", min_value=0, step=1)
                
                if st.button("送出訂單", use_container_width=True, type="primary"):
                    with get_db_conn() as conn:
                        cursor = conn.cursor()
                        if mode == "買入":
                            total_cost = lots * 1000 * price
                            if total_cost <= user_cash and lots > 0:
                                # 買入邏輯
                                old_s = dict(zip(df_h['symbol'], df_h['shares'])).get(stock_input, 0)
                                old_c = dict(zip(df_h['symbol'], df_h['avg_cost'])).get(stock_input, 0)
                                new_s = old_s + (lots * 1000)
                                new_avg = ((old_s * old_c) + total_cost) / new_s
                                
                                cursor.execute("UPDATE users SET cash = ? WHERE username = ?", (user_cash - total_cost, user))
                                cursor.execute("REPLACE INTO holdings VALUES (?, ?, ?, ?)", (user, stock_input, new_s, new_avg))
                                cursor.execute("INSERT INTO trade_history VALUES (?, ?, ?, ?, ?, ?, ?)", 
                                              (user, datetime.now().strftime("%Y-%m-%d %H:%M"), stock_input, "買入", price, lots, 0))
                                conn.commit()
                                st.toast("✅ 交易成功")
                                st.rerun()
                            else: st.error("金額或數量有誤")
                        else:
                            curr_s = dict(zip(df_h['symbol'], df_h['shares'])).get(stock_input, 0)
                            if 0 < (lots * 1000) <= curr_s:
                                # 賣出邏輯
                                avg_c = dict(zip(df_h['symbol'], df_h['avg_cost'])).get(stock_input, 0)
                                pnl = (price - avg_c) * (lots * 1000)
                                
                                cursor.execute("UPDATE users SET cash = ? WHERE username = ?", (user_cash + (lots*1000*price), user))
                                if curr_s - (lots*1000) > 0:
                                    cursor.execute("UPDATE holdings SET shares = ? WHERE username = ? AND symbol = ?", (curr_s - (lots*1000), user, stock_input))
                                else:
                                    cursor.execute("DELETE FROM holdings WHERE username = ? AND symbol = ?", (user, stock_input))
                                cursor.execute("INSERT INTO trade_history VALUES (?, ?, ?, ?, ?, ?, ?)", 
                                              (user, datetime.now().strftime("%Y-%m-%d %H:%M"), stock_input, "賣出", price, lots, pnl))
                                conn.commit()
                                st.toast(f"💰 賣出成功！損益: {pnl:,.0f}")
                                st.rerun()
                            else: st.error("庫存不足")

    # 持股與歷史
    st.divider()
    t1, t2 = st.tabs(["📋 目前持股", "📜 交易紀錄"])
    with t1:
        if not df_h.empty: st.dataframe(df_h[['symbol', 'shares', 'avg_cost']], use_container_width=True)
        else: st.info("尚無持股")
    with t2:
        if not df_t.empty: st.dataframe(df_t.sort_values("time", ascending=False), use_container_width=True)
        else: st.info("尚無紀錄")
    #python3 -m streamlit run server4.py