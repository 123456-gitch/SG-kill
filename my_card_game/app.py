import random
import time
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.config['SECRET_KEY'] = 'sg_ultimate_match_2026_fixed'
socketio = SocketIO(app, cors_allowed_origins="*")

# ==========================================
# 🃏 全量卡牌池数据中心
# ==========================================
BASIC_CARDS = (
    ["攻"] * 15 + ["防"] * 10 + ["长城"] * 4 + ["回血"] * 6 + 
    ["卡牌大师"] * 4 + ["荆轲刺秦"] * 3 + ["一字马"] * 3 + 
    ["顺手牵羊"] * 4 + ["江山易主"] * 2 + ["同归于尽"] * 3
)
STATUS_CARDS = ["背水一战", "饮鸩止渴", "卧薪尝胆", "暗度陈仓"]

class GameEngine:
    def __init__(self):
        self.bot_count = 2
        self.reset_all()

    def reset_all(self):
        self.active = False
        self.players = []       
        self.current_idx = 0    
        self.round = 1
        self.actions_left = 0
        self.deck = []
        self.status_deck = []
        self.pending_action = None
        self.logs = []

game = GameEngine()

@app.route('/')
def index():
    return render_template('index.html')

# ==========================================
# ⚙️ 核心内部辅助业务逻辑
# ==========================================
def add_log(msg):
    game.logs.append(msg)
    socketio.emit('log', {'msg': msg})

def rebuild_decks():
    game.deck = list(BASIC_CARDS)
    random.shuffle(game.deck)
    game.status_deck = list(STATUS_CARDS)
    random.shuffle(game.status_deck)

def draw_cards(player_idx, count):
    if count <= 0: return
    p = game.players[player_idx]
    drawn = []
    for _ in range(count):
        if not game.deck:
            rebuild_decks()
            add_log("🤹 [洗牌] 基本卡牌堆已抽空，重新混洗！")
        if game.deck:
            drawn.append(game.deck.pop(0))
    p['hand'].extend(drawn)

def force_hp_limit(player):
    """强行压制血量不超过其最大上限"""
    if player['hp'] > player['max_hp']:
        player['hp'] = player['max_hp']

def broadcast_lobby():
    humans = [p for p in game.players if not p.get('is_bot')]
    socketio.emit('lobby_update', {
        'count': len(humans),
        'players': [{"name": p['name']} for p in humans],
        'game_active': game.active,
        'bot_count': game.bot_count
    })

def broadcast_state():
    if not game.active: return
    
    for human_player in game.players:
        if human_player.get('is_bot'): continue
        
        human_idx = human_player['idx']
        client_players = []
        
        for p in game.players:
            visible_faction = "隐藏"
            if p['idx'] == human_idx or p['faction_revealed'] or not p['alive']:
                visible_faction = p['faction']
                
            client_players.append({
                "name": p['name'],
                "idx": p['idx'],
                "alive": p['alive'],
                "hp": p['hp'],
                "max_hp": p['max_hp'],
                "faction": visible_faction,
                "hand_count": len(p['hand']),
                "status": p['status'],
                "status_cooldown": p['status_cooldown']
            })
