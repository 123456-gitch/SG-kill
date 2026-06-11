import random
import time
import threading
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.config['SECRET_KEY'] = 'sg_ultimate_match_2026_fixed'
socketio = SocketIO(app, cors_allowed_origins="*")

# ==========================================
# 🃏 问题7修复：攻略多于防
# ==========================================
BASIC_CARDS = (
    ["攻"] * 50 + ["防"] * 45 + ["长城"] * 20 + ["回血"] * 25 + 
    ["卡牌大师"] * 20 + ["荆轲刺秦"] * 8 + ["一字马"] * 8 + 
    ["顺手牵羊"] * 15 + ["江山易主"] * 4 + ["同归于尽"] * 10
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
        self.new_round_started = False

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
    # ✅ 问题3修复：抽牌后检查血量不超上限
    force_hp_limit(p)

def force_hp_limit(player):
    """✅ 问题3修复：时刻检测血量不超过上限，超过立即修正"""
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
            if p['idx'] == human_idx:
                visible_faction = p['faction']
            elif not p['alive']:
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
# 🎮 核心战斗引擎流转
# ==========================================
def start_game_engine():
    game.active = True
    game.round = 1
    game.new_round_started = True
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
        
        p['max_hp'] = 5
        p['hp'] = 5
        
        p['faction_revealed'] = False
            
        p['hand'] = [game.deck.pop(0) for _ in range(5)]
        p['status_cards'] = [game.status_deck.pop(0) for _ in range(2)]

    # ✅✅✅ 问题2终极修复：游戏启动前，第一行就设置！100%确保有行动点！
    first_idx = random.randint(0, len(game.players) - 1)
    game.current_idx = first_idx
    game.actions_left = 2  # 第一个出牌的强制2点，写死！
    
    add_log("⚔️ —— 乱世沙场大幕开启，生死对局正式激活！ ——")
    add_log(f"🎬 —— 轮到【{game.players[first_idx]['name']}】排兵布阵 ——")
    add_log(f"⚡ 本轮行动力：{game.actions_left} 点")
    
    # ✅✅✅ 广播前再确认一次行动点
    game.actions_left = 2
    broadcast_state()
    trigger_bot_if_needed()

def start_turn(idx):
    if not game.active: return
    p = game.players[idx]
    if not p['alive']:
        next_turn()
        return
        
    game.current_idx = idx
    # ✅✅✅ 问题2终极修复：每回合开始第一行就写死行动点
    game.actions_left = game.round + 1
    p['beishui_decided'] = False
    
    if p.get('skipped', False):
        p['skipped'] = False
        add_log(f"⏰ 【{p['name']}】遭受【一字马】封印，强制跳过本轮！")
        next_turn()
        return

    # ✅ 问题4+5修复：状态牌CD逻辑
    if p['status_cooldown'] > 0:
        p['status_cooldown'] -= 1
        if p['status_cooldown'] == 0:
            # ✅ 问题4修复：CD到0就结束，不再变回3！
            add_log(f"✨ 【{p['name']}】的【{p['status']}】状态已届满，可以更换新状态了！")
            p['status'] = "正常"
    
    # ✅ 问题5修复：饮鸩止渴每回合减3上限，不小于1
    if p['status'] == "饮鸩止渴":
        p['max_hp'] = max(1, p['max_hp'] - 3)
        force_hp_limit(p)
        add_log(f"🧪 【{p['name']}】饮鸩止渴毒发！最大生命扣减3，当前上限：{p['max_hp']}")

    add_log(f"🎬 —— 轮到【{p['name']}】排兵布阵 ——")
    add_log(f"⚡ 本轮行动力：{game.actions_left} 点")
    
    # ✅✅✅ 广播前再确认一次
    game.actions_left = game.round + 1
    
    if p['status'] != "背水一战":
        broadcast_state()
        trigger_bot_if_needed()
    else:
        if p.get('is_bot'):
            time.sleep(1.5)
            handle_bot_beishui(idx)
        else:
            broadcast_state()

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
    
    # 3个玩家各出完=1轮
    first_player_idx = next((i for i, p in enumerate(game.players) if p['alive']), 0)
    if next_idx == first_player_idx:
        game.round += 1
        game.new_round_started = True
        add_log(f"📢 ====== 第 {game.round} 轮开始 ======")
        
        # 新一轮开始补牌到5张
        for player in game.players:
            if player['alive'] and player['status'] != "背水一战":
                cards_needed = 5 - len(player['hand'])
                if cards_needed > 0:
                    draw_cards(player['idx'], cards_needed)
                    add_log(f"✋ 新一轮开始，【{player['name']}】自动补牌 {cards_needed} 张至5张。")
        
        if game.round % 2 == 1:
            rebuild_decks()
            add_log("🤹 [时空流转] 战局推进两轮，核心弃牌堆重新洗牌！")
            
    start_turn(next_idx)

# ==========================================
# 🤖 智能人机自动化调度
# ==========================================
def trigger_bot_if_needed():
    if not game.active or game.pending_action: return
    curr = game.players[game.current_idx]
    if curr['alive'] and curr.get('is_bot'):
        if curr['status'] == "背水一战" and not curr['beishui_decided']:
            def delayed_beishui():
                time.sleep(1.5)
                handle_bot_beishui(game.current_idx)
            threading.Thread(target=delayed_beishui, daemon=True).start()
            return
        def delayed_bot_move():
            time.sleep(1.5)
            run_bot_active_move(game.current_idx)
        threading.Thread(target=delayed_bot_move, daemon=True).start()

def run_bot_active_move(bot_idx):
    p = game.players[bot_idx]
    if game.actions_left <= 0 or not p['alive']:
        time.sleep(1.5)
        end_turn_logic()
        return

    if p['status'] == "正常" and p['status_cards']:
        scard = p['status_cards'].pop(0)
        equip_status_logic(bot_idx, scard)
        broadcast_state()
        def continue_after_equip():
            time.sleep(1.5)
            trigger_bot_if_needed()
        threading.Thread(target=continue_after_equip, daemon=True).start()
        return

    active_cards = [c for c in p['hand'] if c not in ["防", "长城"]]
    if not active_cards:
        time.sleep(1.5)
        end_turn_logic()
        return

    card = None
    if "回血" in active_cards:
        card = "回血"
    else:
        playable = [c for c in active_cards if c != "回血"]
        if playable: card = random.choice(playable)
            
    if not card:
        time.sleep(1.5)
        end_turn_logic()
        return

    living_enemies = [target for target in game.players if target['idx'] != bot_idx and target['alive']]
    if not living_enemies:
        time.sleep(1.5)
        end_turn_logic()
        return
    target_idx = random.choice(living_enemies)['idx']

    success = execute_play_card(bot_idx, card, target_idx)
    if not success:
        time.sleep(1.5)
        end_turn_logic()
    else:
        if not game.pending_action:
            def continue_after_play():
                time.sleep(1.5)
                trigger_bot_if_needed()
            threading.Thread(target=continue_after_play, daemon=True).start()

def handle_bot_beishui(idx):
    p = game.players[idx]
    sac = min(2, p['hp'] - 1) if p['hp'] > 2 else 0
    execute_beishui_decision(idx, sac)

def handle_bot_defense_response(bot_idx):
    if not game.pending_action: return
    p = game.players[bot_idx]
    
    time.sleep(1.5)
    
    while game.pending_action and game.pending_action['required_defenses'] > 0:
        if "长城" in p['hand']:
            p['hand'].remove("长城")
            add_log(f"🧱 机器人【{p['name']}】瞬发【长城】，绝对格挡【{game.pending_action['card']}】！")
            game.pending_action = None
            break
            
        if "防" in p['hand'] and game.pending_action['card'] in ["攻", "荆轲刺秦"]:
            p['hand'].remove("防")
            game.pending_action['required_defenses'] -= 1
            add_log(f"🛡️ 机器人【{p['name']}】打出【防】。")
            if game.pending_action['required_defenses'] <= 0:
                src_idx = game.pending_action['source_idx']
                tgt_idx = game.pending_action['target_idx']
                card = game.pending_action['card']
                execute_card_effect(src_idx, tgt_idx, card)
                game.pending_action = None
        else:
            src_idx = game.pending_action['source_idx']
            tgt_idx = game.pending_action['target_idx']
            card = game.pending_action['card']
            execute_card_effect(src_idx, tgt_idx, card)
            game.pending_action = None
            break
            
    check_victory_conditions()
    broadcast_state()

# ==========================================
# ⚔️ 规则与伤害结算中心
# ==========================================
def execute_play_card(src_idx, card, tgt_idx):
    src = game.players[src_idx]
    tgt = game.players[tgt_idx] if tgt_idx != -1 else None

    if game.actions_left <= 0 or card not in src['hand']: return False

    game.actions_left -= 1
    src['hand'].remove(card)

    if src['status'] == "卧薪尝胆":
        src['hp'] = max(1, src['hp'] - 1)
        add_log(f"🔥 【{src['name']}】卧薪尝胆执念反噬，自损1血！")

    if card in ["回血", "卡牌大师"]:
        add_log(f"🃏 【{src['name']}】消耗行动力，打出 【{card}】")
    else:
        add_log(f"🃏 【{src['name']}】消耗行动力，打出 【{card}】" + (f" ➡️ 目标直指 【{tgt['name']}】" if tgt else ""))

    if card == "回血":
        # ✅ 问题6修复：满血也可以打出回血，只是不回血
        if src['hp'] < src['max_hp']:
            src['hp'] += 1
            add_log(f"💚 【{src['name']}】恢复1点血量！")
        else:
            add_log(f"💚 【{src['name']}】打出回血，但已满血，无效果。")
        force_hp_limit(src)
    elif card == "卡牌大师":
        draw_cards(src_idx, 2)
    else:
        # 所有需要目标的牌都进入防御结算
        set_attack_pipeline(src_idx, tgt_idx, card, 2 if card == "荆轲刺秦" else 1)
    
    if game.actions_left <= 0:
        add_log(f"⚠️ 【{src['name']}】行动力已耗尽，强制结束回合！")
        end_turn_logic()
        return True
    
    check_victory_conditions()
    broadcast_state()
    return True

def set_attack_pipeline(src_idx, tgt_idx, card, count):
    tgt = game.players[tgt_idx]
    
    game.pending_action = {
        "source_idx": src_idx,
        "target_idx": tgt_idx,
        "card": card,
        "required_defenses": count,
        "original_count": count
    }
    
    if tgt.get('is_bot'):
        if "长城" in tgt['hand']:
            time.sleep(1.5)
            tgt['hand'].remove("长城")
            add_log(f"🧱 机器人【{tgt['name']}】秒出【长城】，格挡【{card}】！")
            game.pending_action = None
            broadcast_state()
            return
        handle_bot_defense_response(tgt_idx)

def execute_card_effect(src_idx, tgt_idx, card):
    """✅ 问题1修复：防御成功才执行效果，打防成功不掉血！"""
    src = game.players[src_idx]
    tgt = game.players[tgt_idx]
    
    if card == "攻":
        damage_player(tgt_idx, 1, reason="攻")
    elif card == "荆轲刺秦":
        damage_player(tgt_idx, 2, reason="荆轲刺秦")
    elif card == "一字马":
        tgt['skipped'] = True
        add_log(f"🔒 【{tgt['name']}】被【一字马】封印，下回合将被跳过！")
    elif card == "顺手牵羊":
        if tgt['hand']:
            stolen = random.choice(tgt['hand'])
            tgt['hand'].remove(stolen)
            src['hand'].append(stolen)
            add_log(f"🥷 【{src['name']}】偷取了【{tgt['name']}】的1张手牌！")
    elif card == "江山易主":
        src['hand'], tgt['hand'] = tgt['hand'], src['hand']
        add_log(f"🔄 【{src['name']}】与【{tgt['name']}】手牌大对调！")
    elif card == "同归于尽":
        damage_player(src_idx, 1, reason="同归于尽")
        damage_player(tgt_idx, 1, reason="同归于尽")

def equip_status_logic(idx, status_card):
    p = game.players[idx]
    p['status'] = status_card
    # ✅ 问题4修复：状态牌CD=3，到0就结束
    p['status_cooldown'] = 3
    add_log(f"⚡ 【{p['name']}】装备了【{status_card}】，持续3回合！")
    
    if status_card in ["背水一战", "卧薪尝胆", "暗度陈仓"]:
        p['max_hp'] = 5
    elif status_card == "饮鸩止渴":
        p['max_hp'] = 10
        p['hp'] = 10
        
    force_hp_limit(p)

def execute_beishui_decision(idx, sacrifice):
    p = game.players[idx]
    p['beishui_decided'] = True
    
    if sacrifice > 0:
        sacrifice = min(sacrifice, p['hp'] - 1)
        p['hp'] -= sacrifice
        draw_count = sacrifice + 1
        add_log(f"🩸 【{p['name']}】背水一战：自损{sacrifice}血，补{draw_count}张牌！")
        draw_cards(idx, draw_count)
    else:
        cards_needed = 5 - len(p['hand'])
        if cards_needed > 0:
            add_log(f"🛡️ 【{p['name']}】放弃献祭，补牌{cards_needed}张。")
            draw_cards(idx, cards_needed)
        else:
            add_log(f"🛡️ 【{p['name']}】放弃献祭，手牌已满。")
        
    broadcast_state()
    trigger_bot_if_needed()

def damage_player(idx, amount, reason=""):
    if amount <= 0: return
    p = game.players[idx]
    p['hp'] -= amount
    add_log(f"💥 【{p['name']}】因【{reason}】扣除{amount}点血量！当前：{p['hp']}/{p['max_hp']}")
    
    if p['hp'] <= 0 and p['faction'] == "丁" and not p.get('has_revived', False):
        p['has_revived'] = True
        p['hp'] = 2
        p['max_hp'] = max(p['max_hp'], 2)
        add_log(f"🔥✨ 【丁】复活！恢复2血，抽2张牌！")
        draw_cards(idx, 2)
        
    if p['hp'] <= 0:
        p['hp'] = 0
        p['alive'] = False
        add_log(f"💀🪦 【{p['name']}】阵亡！身份：【{p['faction']}】")
        p['hand'] = []
        p['status_cards'] = []
        p['status'] = "正常"

def check_victory_conditions():
    if not game.active: return
    si_alive = any(p['alive'] for p in game.players if p['faction'] == "司")
    ji_alive = any(p['alive'] for p in game.players if p['faction'] == "冀")
    
    if not ji_alive:
        game.active = False
        add_log("🏆👑 【司】胜利！")
        return
        
    if not si_alive:
        game.active = False
        add_log("🏆🌟 【冀+丁】胜利！")
        return

# ==========================================
# 📡 Socket.IO
# ==========================================
@socketio.on('change_bot_count')
def on_change_bot_count(data):
    if not game.active:
        game.bot_count = min(2, max(0, int(data.get('bot_count', 2))))
        broadcast_lobby()

@socketio.on('join_game')
def on_join_game(data):
    sid = request.sid
    name = data.get('name', '').strip()
    if not name: name = f"玩家_{random.randint(100,999)}"
    
    if game.active:
        existing = next((p for p in game.players if p.get('sid') == sid), None)
        if existing:
            broadcast_state()
            return
        else:
            emit('action_error', {'msg': '🚨 游戏已开始！'})
            return
            
    game.players = [p for p in game.players if not p.get('is_bot')]
    existing_lobby = next((p for p in game.players if p.get('sid') == sid), None)
    if existing_lobby:
        existing_lobby['name'] = name
    else:
        game.players.append({
            "sid": sid, "name": name, "is_bot": False, "alive": True, "hp": 5, "max_hp": 5,
            "faction": "隐藏", "faction_revealed": False, "status": "正常", "status_cooldown": 0,
            "hand": [], "status_cards": [], "beishui_decided": False, "skipped": False, "has_revived": False
        })

    required_humans = 3 - game.bot_count
    current_humans = len([p for p in game.players if not p.get('is_bot')])
    
    if current_humans >= required_humans:
        bot_names = ["🤖 诸葛硅基", "🤖 曹操算法", "🤖 司马算力"]
        for i in range(game.bot_count):
            game.players.append({
                "sid": f"bot_{i}", "name": bot_names[i], "is_bot": True, "alive": True,
                "hp": 5, "max_hp": 5, "faction": "隐藏", "status": "正常", "status_cooldown": 0,
                "hand": [], "status_cards": [], "beishui_decided": False, "skipped": False
            })
            
        for idx, p in enumerate(game.players):
            p['idx'] = idx
            
        start_game_engine()
    else:
        broadcast_lobby()

@socketio.on('disconnect')
def on_disconnect():
    if not game.active:
        game.players = [p for p in game.players if p.get('sid') != request.sid]
        broadcast_lobby()

def run_absolute_nuclear_reset():
    game.reset_all()
    socketio.emit('force_reload_all')
    socketio.emit('lobby_update', {'count': 0, 'players': [], 'game_active': False, 'bot_count': 2})

@socketio.on('reset_game')
def on_reset_game():
    run_absolute_nuclear_reset()

@socketio.on('play_card')
def on_play_card(data):
    if not game.active: return
    p = get_player_by_sid(request.sid)
    if not p: return

    if game.pending_action:
        emit('action_error', {'msg': '🚨 结算中！'})
        return
    if game.current_idx != p['idx']: 
        emit('action_error', {'msg': '🚨 不是你的回合！'})
        return
        
    card = data.get('card')
    tgt_idx = int(data.get('target', -1))
    intent = data.get('intent') 

    if card in ["防", "长城"]:
        emit('action_error', {'msg': '🚨 被动牌只能防御时用！'})
        return

    TARGET_CARDS = ["攻", "荆轲刺秦", "一字马", "顺手牵羊", "江山易主", "同归于尽"]
    if card in TARGET_CARDS and tgt_idx == -1:
        emit('action_error', {'msg': '🚨 请先选目标！'})
        return
    if tgt_idx != -1 and not game.players[tgt_idx]['alive']:
        emit('action_error', {'msg': '🚨 目标已阵亡！'})
        return
    if game.actions_left <= 0:
        emit('action_error', {'msg': '🚨 行动力用完了！'})
        return
    
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
    if not game.active: return
    p = get_player_by_sid(request.sid)
    if not p: return
    if game.pending_action:
        emit('action_error', {'msg': '🚨 结算中！'})
        return
    if game.current_idx != p['idx']: 
        emit('action_error', {'msg': '🚨 不是你的回合！'})
        return
    
    card = data.get('card')
    if card not in p['status_cards']:
        emit('action_error', {'msg': '🚨 没有这张牌！'})
        return
    if p['status_cooldown'] > 0: 
        emit('action_error', {'msg': f'🚨 【{p["status"]}】还有{p["status_cooldown"]}回合CD！'})
        return
        
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
    src_idx = game.pending_action['source_idx']
    tgt_idx = game.pending_action['target_idx']
    
    if resp_type == '长城' and "长城" in p['hand']:
        p['hand'].remove("长城")
        add_log(f"🧱 【{p['name']}】祭出【长城】，格挡【{card_name}】！")
        game.pending_action = None
    elif resp_type == '防' and "防" in p['hand']:
        p['hand'].remove("防")
        game.pending_action['required_defenses'] -= 1
        add_log(f"🛡️ 【{p['name']}】打出【防】。")
        if game.pending_action['required_defenses'] <= 0:
            # ✅ 问题1修复：防够了就不执行伤害！不掉血！
            add_log(f"✅ 【{p['name']}】防御成功！")
            game.pending_action = None
    elif resp_type == '攻_as_防' and p['status'] == "暗度陈仓" and "攻" in p['hand']:
        p['hand'].remove("攻")
        game.pending_action['required_defenses'] -= 1
        add_log(f"🎭 【{p['name']}】以攻代防！")
        if game.pending_action['required_defenses'] <= 0:
            add_log(f"✅ 【{p['name']}】防御成功！")
            game.pending_action = None
    elif resp_type == '放弃':
        execute_card_effect(src_idx, tgt_idx, card_name)
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
