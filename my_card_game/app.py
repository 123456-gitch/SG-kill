import random
import time
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.config['SECRET_KEY'] = 'sg_ultimate_match_2026'
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
        self.bot_count = 2      # 默认选择两个人机
        self.reset_all()

    def reset_all(self):
        """完全格式化房间状态"""
        self.active = False
        self.players = []       # 混合存储当前的真人与人机
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
    p = game.players[player_idx]
    drawn = []
    for _ in range(count):
        if not game.deck:
            rebuild_decks()
            add_log("🤹 [核心通知] 基本卡牌堆已抽空，核心弃牌堆自动重混洗！")
        if game.deck:
            drawn.append(game.deck.pop(0))
    p['hand'].extend(drawn)

def broadcast_lobby():
    """实时向大厅同步当前的真人玩家就坐情况"""
    humans = [p for p in game.players if not p.get('is_bot')]
    socketio.emit('lobby_update', {
        'count': len(humans),
        'players': [{"name": p['name']} for p in humans],
        'game_active': game.active,
        'bot_count': game.bot_count
    })

def broadcast_state():
    """阵营与暗盘手牌严格隔离的精准同步机制（多真人互看不到彼此手牌）"""
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
            
        is_my_response = False
        pending_card = ""
        required_defenses = 0
        if game.pending_action and game.pending_action['target_idx'] == human_idx:
            is_my_response = True
            pending_card = game.pending_action['card']
            required_defenses = game.pending_action['required_defenses']
            
        is_beishui_prompt = False
        if human_player['status'] == "背水一战" and game.current_idx == human_idx and not human_player['beishui_decided']:
            is_beishui_prompt = True

        socketio.emit('game_update', {
            "round": game.round,
            "current_idx": game.current_idx,
            "actions_left": game.actions_left,
            "deck_count": len(game.deck),
            "my_idx": human_idx,
            "players": client_players,
            "my_cards": human_player['hand'],
            "my_status_cards": human_player['status_cards'],
            "my_status": human_player['status'],
            "is_my_response": is_my_response,
            "pending_card": pending_card,
            "required_defenses": required_defenses,
            "is_beishui_prompt": is_beishui_prompt
        }, to=human_player['sid'])

def get_player_by_sid(sid):
    return next((p for p in game.players if p.get('sid') == sid), None)

# ==========================================
# 🎮 核心战斗生命周期引擎
# ==========================================
def start_game_engine():
    game.active = True
    game.round = 1
    game.logs = []
    rebuild_decks()
    
    factions = ["司", "冀", "丁"]
    random.shuffle(factions)
    
    for i, p in enumerate(game.players):
        p['faction'] = factions[i]
        p['alive'] = True
        p['status'] = "正常"
        p['status_cooldown'] = 0
        p['beishui_decided'] = False
        p['skipped'] = False
        p['has_revived'] = False
        
        if p['faction'] == "冀":
            p['max_hp'] = 4
            p['hp'] = 4
            p['faction_revealed'] = True 
        else:
            p['max_hp'] = 3
            p['hp'] = 3
            p['faction_revealed'] = False
            
        p['hand'] = [game.deck.pop(0) for _ in range(4)]
        p['status_cards'] = [game.status_deck.pop(0) for _ in range(2)]

    lord_idx = next((i for i, p in enumerate(game.players) if p['faction'] == "冀"), 0)
    add_log("⚔️ —— 乱世沙场大幕开启，生死对局正式激活！ ——")
    add_log("📜 天命大纲：【冀】与【丁】结盟共存亡，【司】需只身狙杀【冀】方能颠覆天下！")
    
    start_turn(lord_idx)

def start_turn(idx):
    if not game.active: return
    p = game.players[idx]
    if not p['alive']:
        next_turn()
        return
        
    game.current_idx = idx
    game.actions_left = 2 
    p['beishui_decided'] = False
    
    if p.get('skipped', False):
        p['skipped'] = False
        add_log(f"⏰ 【{p['name']}】遭受【一字马】战术封印，强制跳过本轮出手权！")
        next_turn()
        return

    if p['status'] == "饮鸩止渴":
        p['status_cooldown'] -= 1
        if p['status_cooldown'] <= 0:
            p['max_hp'] = max(1, p['max_hp'] - 2)
            p['hp'] = min(p['max_hp'], p['hp'] + 2)
            add_log(f"🧪 【{p['name']}】饮鸩止渴效果毒发！最大生命永久扣减2，强行拔高回血2点。")
            p['status_cooldown'] = 3 
    elif p['status_cooldown'] > 0:
        p['status_cooldown'] -= 1
        if p['status_cooldown'] == 0:
            p['status'] = "正常"
            add_log(f"✨ 【{p['name']}】的特权技能状态届满卸载，恢复凡人常态。")

    add_log(f"🎬 —— 轮到【{p['name']}】排兵布阵 ——")
    
    if p['status'] == "背水一战":
        if p.get('is_bot'):
            handle_bot_beishui(idx)
        else:
            broadcast_state()
    else:
        draw_cards(idx, 1) 
        broadcast_state()
        trigger_bot_if_needed()

def end_turn_logic():
    if not game.active: return
    add_log(f"🏁 【{game.players[game.current_idx]['name']}】鸣金收兵，回合移交。")
    next_turn()

def next_turn():
    if not game.active: return
    attempts = 0
    next_idx = game.current_idx
    while attempts < 4:
        next_idx = (next_idx + 1) % len(game.players)
        if game.players[next_idx]['alive']:
            break
        attempts += 1
        
    if next_idx == game.current_idx: return
    
    if next_idx == 0:
        game.round += 1
        if game.round % 2 == 1:
            rebuild_decks()
            add_log("🤹 [时空流转] 战局推进两轮，核心弃牌堆重新洗牌！")
            
    start_turn(next_idx)

# ==========================================
# 🤖 智能人机自动化调度中枢
# ==========================================
def trigger_bot_if_needed():
    if not game.active or game.pending_action: return
    curr = game.players[game.current_idx]
    if curr['alive'] and curr.get('is_bot'):
        if curr['status'] == "背水一战" and not curr['beishui_decided']:
            handle_bot_beishui(game.current_idx)
            return
        run_bot_active_move(game.current_idx)

def run_bot_active_move(bot_idx):
    p = game.players[bot_idx]
    if game.actions_left <= 0 or not p['alive']:
        end_turn_logic()
        return

    if p['status'] == "正常" and p['status_cards']:
        scard = p['status_cards'].pop(0)
        equip_status_logic(bot_idx, scard)
        broadcast_state()
        trigger_bot_if_needed()
        return

    active_cards = [c for c in p['hand'] if c not in ["防", "长城"]]
    if not active_cards:
        end_turn_logic()
        return

    card = None
    if "回血" in active_cards and p['hp'] < p['max_hp']:
        card = "回血"
    else:
        playable = [c for c in active_cards if c != "回血"]
        if playable: card = playable[0]
            
    if not card:
        end_turn_logic()
        return

    living_enemies = [target for target in game.players if target['idx'] != bot_idx and target['alive']]
    if not living_enemies:
        end_turn_logic()
        return
    target_idx = random.choice(living_enemies)['idx']

    success = execute_play_card(bot_idx, card, target_idx)
    if not success:
        end_turn_logic()
    else:
        if not game.pending_action:
            trigger_bot_if_needed()

def handle_bot_beishui(idx):
    p = game.players[idx]
    sac = min(2, p['hp'] - 1) if p['hp'] > 2 else 0
    execute_beishui_decision(idx, sac)

def handle_bot_defense_response(bot_idx):
    if not game.pending_action: return
    p = game.players[bot_idx]
    
    while game.pending_action and game.pending_action['required_defenses'] > 0:
        if "长城" in p['hand']:
            p['hand'].remove("长城")
            add_log(f"🧱 机器人【{p['name']}】瞬发【长城】叹息之壁，绝对格挡！")
            game.pending_action = None
            break
            
        if "防" in p['hand']:
            p['hand'].remove("防")
            game.pending_action['required_defenses'] -= 1
            add_log(f"🛡️ 机器人【{p['name']}】闪避成功。")
        elif p['status'] == "暗度陈仓" and "攻" in p['hand']:
            p['hand'].remove("攻")
            game.pending_action['required_defenses'] -= 1
            add_log(f"🎭 机器人【{p['name']}】凭【暗度陈仓】偷梁换柱，拿【攻】当【防】用！")
        else:
            card_name = game.pending_action['card']
            dmg = 2 if card_name == "荆轲刺秦" else 1
            if p['status'] == "卧薪尝胆" and card_name == "攻":
                dmg = max(0, dmg - 1)
            damage_player(bot_idx, dmg, reason=card_name)
            game.pending_action = None
            break
            
    if game.pending_action and game.pending_action['required_defenses'] <= 0:
        game.pending_action = None
        
    check_victory_conditions()
    broadcast_state()

# ==========================================
# ⚔️ 规则与伤害核心结算中心
# ==========================================
def execute_play_card(src_idx, card, tgt_idx):
    src = game.players[src_idx]
    tgt = game.players[tgt_idx] if tgt_idx != -1 else None

    if game.actions_left <= 0 or card not in src['hand']: return False

    game.actions_left -= 1
    src['hand'].remove(card)

    if src['status'] == "卧薪尝胆":
        damage_player(src_idx, 1, reason="卧薪尝胆出牌反噬")

    add_log(f"🃏 【{src['name']}】打出 【{card}】" + (f" ➡️ 目标直指 【{tgt['name']}】" if tgt else ""))

    if card == "回血":
        src['hp'] = min(src['max_hp'], src['hp'] + 1)
    elif card == "卡牌大师":
        draw_cards(src_idx, 2)
    elif card == "攻":
        set_attack_pipeline(src_idx, tgt_idx, "攻", 1)
    elif card == "荆轲刺秦":
        set_attack_pipeline(src_idx, tgt_idx, "荆轲刺秦", 2)
        damage_player(src_idx, 1, reason="荆轲刺秦刺客反噬")
    elif card == "一字马":
        tgt['skipped'] = True
    elif card == "顺手牵羊":
        if tgt['hand']:
            stolen = random.choice(tgt['hand'])
            tgt['hand'].remove(stolen)
            src['hand'].append(stolen)
            add_log(f"🥷 偷取了 【{tgt['name']}】 的 1 张随机手牌！")
    elif card == "江山易主":
        src['hand'], tgt['hand'] = tgt['hand'], src['hand']
        add_log(f"🔄 乾坤逆转，双方所有手牌爆发大对调！")
    elif card == "同归于尽":
        damage_player(src_idx, 1, reason="同归于尽")
        damage_player(tgt_idx, 1, reason="同归于尽")
    
    check_victory_conditions()
    broadcast_state()
    return True

def set_attack_pipeline(src_idx, tgt_idx, card, count):
    tgt = game.players[tgt_idx]
    if tgt.get('is_bot') and "长城" in tgt['hand']:
        tgt['hand'].remove("长城")
        add_log(f"🧱 机器人【{tgt['name']}】触发防御直觉，秒出【长城】规避伤害！")
        return

    game.pending_action = {
        "source_idx": src_idx,
        "target_idx": tgt_idx,
        "card": card,
        "required_defenses": count
    }
    if tgt.get('is_bot'):
        handle_bot_defense_response(tgt_idx)

def equip_status_logic(idx, status_card):
    p = game.players[idx]
    p['status'] = status_card
    add_log(f"⚡ 【{p['name']}】驱动装备了高级特权状态：【{status_card}】！")
    
    if status_card in ["背水一战", "卧薪尝胆"]:
        p['max_hp'] = 5
        p['status_cooldown'] = 3
    elif status_card == "饮鸩止渴":
        p['max_hp'] = 10
        p['hp'] = 10
        p['status_cooldown'] = 3
    elif status_card == "暗度陈仓":
        p['status_cooldown'] = 3

def execute_beishui_decision(idx, sacrifice):
    p = game.players[idx]
    p['beishui_decided'] = True
    
    if sacrifice > 0:
        sacrifice = min(sacrifice, p['hp'] - 1)
        p['hp'] -= sacrifice
        draw_count = sacrifice + 1
        add_log(f"🩸 【{p['name']}】背水一战爆发：献祭 {sacrifice} 血量，疯狂补充 {draw_count} 张牌！")
        draw_cards(idx, draw_count)
    else:
        add_log(f"🛡️ 【{p['name']}】选择低调潜伏，不执行献祭，常规摸牌 1 张。")
        draw_cards(idx, 1)
        
    broadcast_state()
    trigger_bot_if_needed()

def damage_player(idx, amount, reason=""):
    if amount <= 0: return
    p = game.players[idx]
    p['hp'] -= amount
    add_log(f"💥 【{p['name']}】由于【{reason}】受到了 {amount} 点重大创伤！")
    
    if p['hp'] <= 0 and p['faction'] == "丁" and not p.get('has_revived', False):
        p['has_revived'] = True
        p['faction_revealed'] = True
        p['hp'] = 2
        p['max_hp'] = max(p['max_hp'], 2)
        add_log(f"🔥✨ 绝境逢生！隐修真身【丁】复苏！【{p['name']}】原地复活恢复2血并强抽2张牌！")
        draw_cards(idx, 2)
        
    if p['hp'] <= 0:
        p['hp'] = 0
        p['alive'] = False
        p['faction_revealed'] = True
        add_log(f"💀🪦 讣告：【{p['name']}】力战阵亡！真实所属牌子为：【{p['faction']}】")
        p['hand'] = []
        p['status_cards'] = []
        p['status'] = "正常"

def check_victory_conditions():
    if not game.active: return
    si_alive = any(p['alive'] for p in game.players if p['faction'] == "司")
    ji_alive = any(p['alive'] for p in game.players if p['faction'] == "冀")
    
    if not ji_alive:
        game.active = False
        add_log("🏆👑 【斩首胜利】叛逆逆袭成功！乱臣贼子【司】斩杀主公，夺取大好江山！")
        return
        
    if not si_alive:
        game.active = False
        add_log("🏆🌟 【正统胜利】星图永固！保皇军【冀】与【丁】大获全胜，成功铲除叛贼【司】！")
        return

# ==========================================
# 📡 Socket.IO 匹配看门狗网关
# ==========================================
@socketio.on('change_bot_count')
def on_change_bot_count(data):
    """玩家在前端大厅勾选人机数量选项"""
    if not game.active:
        game.bot_count = min(2, max(0, int(data.get('bot_count', 2))))
        broadcast_lobby()

@socketio.on('join_game')
def on_join_game(data):
    """【核心重构】：根据你选定的人机数量，动态自动触发看门狗判断"""
    sid = request.sid
    name = data.get('name', '').strip()
    if not name: name = f"玩家_{random.randint(100,999)}"
    
    # 1. 安全防护门：如果游戏处于激战态，原班人马刷新网页可以重连，外人不可进
    if game.active:
        existing = next((p for p in game.players if p.get('sid') == sid), None)
        if existing:
            broadcast_state()
            return
        else:
            emit('action_error', {'msg': '🚨 战局已开启。如果由于之前的死局导致无法重试，请点击【重置房间】按钮。'})
            return
            
    # 2. 移除上轮遗留的人机垃圾数据，纯净大厅
    game.players = [p for p in game.players if not p.get('is_bot')]
    
    # 3. 将真人玩家塞入待命队列
    existing_lobby = next((p for p in game.players if p.get('sid') == sid), None)
    if existing_lobby:
        existing_lobby['name'] = name
    else:
        game.players.append({
            "sid": sid, "name": name, "is_bot": False, "alive": True, "hp": 3, "max_hp": 3,
            "faction": "隐藏", "faction_revealed": False, "status": "正常", "status_cooldown": 0,
            "hand": [], "status_cards": [], "beishui_decided": False, "skipped": False, "has_revived": False
        })

    # 🎯 核心看门狗联动：计算启动对局需要集结几个真人 (Required = 3 - Bots)
    required_humans = 3 - game.bot_count
    current_humans = len([p for p in game.players if not p.get('is_bot')])
    
    if current_humans >= required_humans:
        # 【条件触发】：真人到齐了！用选定的人机数量补齐空位，原地立刻自动开局！
        bot_names = ["🤖 诸葛硅基", "🤖 曹操算法", "🤖 司马算力"]
        for i in range(game.bot_count):
            game.players.append({
                "sid": f"bot_sid_{i}_{int(time.time())}", "name": bot_names[i], "is_bot": True, "alive": True,
                "hp": 3, "max_hp": 3, "faction": "隐藏", "faction_revealed": False, "status": "正常",
                "status_cooldown": 0, "hand": [], "status_cards": [], "beishui_decided": False, "skipped": False, "has_revived": False
            })
            
        # 全员绑定战场行动顺序索引
        for idx, p in enumerate(game.players):
            p['idx'] = idx
            
        start_game_engine()
    else:
        # 真人还没凑齐设定人数，继续在大厅挂牌等待
        broadcast_lobby()

@socketio.on('disconnect')
def on_disconnect():
    """断线看门狗：大厅里的玩家如果关了网页或刷新，主动释放席位，防止卡位死锁"""
    if not game.active:
        game.players = [p for p in game.players if p.get('sid') != request.sid]
        broadcast_lobby()

# ==========================================
# ⚡️ 核心熔断级暴力重置网关（完美根除任何卡房）
# ==========================================
def run_absolute_nuclear_reset():
    """最高禁咒级重置：碾碎一切后端死结，并广播前端深度重载同步"""
    game.reset_all()
    # 广播全场最高级别清场通知
    socketio.emit('log', {'msg': "🔄 警报！房主执行了最高权限【暴力重置】！全房间死锁已被无条件熔断抹平！"})
    socketio.emit('force_reload_all') # 触发前端最稳健的核级武器：window.location.reload()
    socketio.emit('game_reset')       
    socketio.emit('room_reset')       
    # 重构广播完全清空的大厅基础视图
    socketio.emit('lobby_update', {
        'count': 0, 'players': [], 'game_active': False, 'bot_count': game.bot_count
    })

@socketio.on('reset_game')
def on_reset_game():
    run_absolute_nuclear_reset()

@socketio.on('reset_room')
def on_reset_room():
    run_absolute_nuclear_reset()

# ==========================================
# 🎮 其他游戏内常规操作指令网关
# ==========================================
@socketio.on('play_card')
def on_play_card(data):
    if not game.active or game.pending_action: return
    p = get_player_by_sid(request.sid)
    if not p or game.current_idx != p['idx']: return
    
    card = data.get('card')
    tgt_idx = data.get('target', -1)
    intent = data.get('intent') 
    
    if p['status'] == "暗度陈仓" and intent == "攻" and card == "防":
        if "防" in p['hand']:
            p['hand'].remove("防")
            p['hand'].append("攻")
            card = "攻"

    success = execute_play_card(p['idx'], card, tgt_idx)
    if success and not game.pending_action:
        trigger_bot_if_needed()

@socketio.on('equip_status')
def on_equip_status(data):
    if not game.active or game.pending_action: return
    p = get_player_by_sid(request.sid)
    if not p or game.current_idx != p['idx']: return
    
    card = data.get('card')
    if card not in p['status_cards'] or p['status_cooldown'] > 0: return
        
    p['status_cards'].remove(card)
    equip_status_logic(p['idx'], card)
    broadcast_state()
    trigger_bot_if_needed()

@socketio.on('respond_action')
def on_respond_action(data):
    if not game.active or not game.pending_action: return
    p = get_player_by_sid(request.sid)
    if not p or game.pending_action['target_idx'] != p['idx']: return
    
    resp_type = data.get('type') 
    card_name = game.pending_action['card']
    
    if resp_type == '长城' and "长城" in p['hand']:
        p['hand'].remove("长城")
        add_log(f"🧱 【{p['name']}】使用【长城】绝对防御！")
        game.pending_action = None
    elif resp_type == '防' and "防" in p['hand']:
        p['hand'].remove("防")
        game.pending_action['required_defenses'] -= 1
        add_log(f"🛡️ 【{p['name']}】使用【防】。")
    elif resp_type == '攻_as_防' and p['status'] == "暗度陈仓" and "攻" in p['hand']:
        p['hand'].remove("攻")
        game.pending_action['required_defenses'] -= 1
        add_log(f"🎭 【{p['name']}】触发【暗度陈仓】指鹿为马，用【攻】当做【防】偏转了伤害！")
    elif resp_type == '放弃':
        dmg = 2 if card_name == "荆轲刺秦" else 1
        if p['status'] == "卧薪尝胆" and card_name == "攻":
            dmg = max(0, dmg - 1)
        damage_player(p['idx'], dmg, reason=card_name)
        game.pending_action = None
        
    if game.pending_action and game.pending_action['required_defenses'] <= 0:
        game.pending_action = None

    check_victory_conditions()
    broadcast_state()
    trigger_bot_if_needed()

@socketio.on('beishui_decision')
def on_beishui_decision(data):
    if not game.active: return
    p = get_player_by_sid(request.sid)
    if not p or game.current_idx != p['idx']: return
    if p['status'] != "背水一战" or p['beishui_decided']: return
    
    sacrifice = int(data.get('sacrifice', 0))
    execute_beishui_decision(p['idx'], sacrifice)

@socketio.on('end_turn')
def on_end_turn():
    p = get_player_by_sid(request.sid)
    if not p or game.current_idx != p['idx']: return
    end_turn_logic()

if __name__ == '__main__':
    socketio.run(app, debug=True, port=5000)
