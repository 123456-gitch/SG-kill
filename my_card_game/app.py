import random
import time
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.config['SECRET_KEY'] = 'sg_multiplayer_ultimate_2026'
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
        self.reset_all()

    def reset_all(self):
        """全房间彻底格式化"""
        self.active = False
        self.players = []       # 混合存储真人与人机
        self.current_idx = 0    # 轮到谁出牌的指针
        self.round = 1
        self.actions_left = 0
        self.deck = []
        self.status_deck = []
        self.pending_action = None
        self.logs = []

    def reset_game_only(self):
        """核心修复：暴力重置对局，保留大厅内的真人玩家，清空人机"""
        self.active = False
        self.current_idx = 0
        self.round = 1
        self.actions_left = 0
        self.deck = []
        self.status_deck = []
        self.pending_action = None
        
        # 只保留真人，清除临时补位的人机
        self.players = [p for p in self.players if not p.get('is_bot', False)]
        
        # 重置真人的基础游戏状态，恢复到大厅待命标准
        for p in self.players:
            p.update({
                "alive": True, "hp": 3, "max_hp": 3, "faction": "隐藏", 
                "faction_revealed": False, "status": "正常", "status_cooldown": 0,
                "hand": [], "status_cards": [], "beishui_decided": False, 
                "skipped": False, "has_revived": False
            })
        self.logs.append("🔄 【系统通知】房主执行了最高级熔断重置！人机已被驱逐，全员返回大厅准备界面！")

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
            add_log("🤹 [核心通知] 基本卡牌堆空了，重新洗牌！")
        if game.deck:
            drawn.append(game.deck.pop(0))
    p['hand'].extend(drawn)

def broadcast_lobby():
    """广播大厅当前的真人集结情况"""
    humans = [p for p in game.players if not p.get('is_bot')]
    socketio.emit('lobby_update', {
        'count': len(humans),
        'players': [{"name": p['name']} for p in humans],
        'game_active': game.active
    })

def broadcast_state():
    """阵营与手牌情报完全隔离的精准多端同步"""
    if not game.active: return
    
    # 遍历所有真人玩家，分别计算他们视角下的战局并私密发送
    for human_player in game.players:
        if human_player.get('is_bot'): continue
        
        human_idx = human_player['idx']
        client_players = []
        
        for p in game.players:
            visible_faction = "隐藏"
            # 只有自己、被揭露的阵营、或者死者才显示真实阵营
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

        # 精准投递到对应真人的 Socket 通道上
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
    add_log("⚔️ —— 三国杀终极乱世战场正式开辟！ ——")
    add_log("📜 天命昭示：【冀】与【丁】结盟互保，【司】需孤身击杀【冀】以夺天下！")
    
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
        add_log(f"⏰ 【{p['name']}】受【一字马】封印，被强行跳过本回合！")
        next_turn()
        return

    # 状态结算
    if p['status'] == "饮鸩止渴":
        p['status_cooldown'] -= 1
        if p['status_cooldown'] <= 0:
            p['max_hp'] = max(1, p['max_hp'] - 2)
            p['hp'] = min(p['max_hp'], p['hp'] + 2)
            add_log(f"🧪 【{p['name']}】饮鸩止渴毒发！最大生命-2，强行回血2点。")
            p['status_cooldown'] = 3 
    elif p['status_cooldown'] > 0:
        p['status_cooldown'] -= 1
        if p['status_cooldown'] == 0:
            p['status'] = "正常"
            add_log(f"✨ 【{p['name']}】的装备状态效果结束，恢复常态。")

    add_log(f"🎬 —— 当前轮到【{p['name']}】开始行动 ——")
    
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
    add_log(f"🏁 【{game.players[game.current_idx]['name']}】宣布回合结束。")
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
            add_log("🤹 [洗牌] 触发交替节点，核心弃牌重新混洗！")
            
    start_turn(next_idx)

# ==========================================
# 🤖 智能补位人机判定中心
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

    # 核心升级：人机会随机锁定任意一个依然存活的敌对目标（完美支持多真人博弈）
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
            add_log(f"🧱 人机【{p['name']}】瞬发【长城】，直接格挡本次攻击！")
            game.pending_action = None
            break
            
        if "防" in p['hand']:
            p['hand'].remove("防")
            game.pending_action['required_defenses'] -= 1
            add_log(f"🛡️ 人机【{p['name']}】打出【防】抵消伤害。")
        elif p['status'] == "暗度陈仓" and "攻" in p['hand']:
            p['hand'].remove("攻")
            game.pending_action['required_defenses'] -= 1
            add_log(f"🎭 人机【{p['name']}】借【暗度陈仓】以【攻】代【防】！")
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
# ⚔️ 核心规则结算中心
# ==========================================
def execute_play_card(src_idx, card, tgt_idx):
    src = game.players[src_idx]
    tgt = game.players[tgt_idx] if tgt_idx != -1 else None

    if game.actions_left <= 0 or card not in src['hand']: return False

    game.actions_left -= 1
    src['hand'].remove(card)

    if src['status'] == "卧薪尝胆":
        damage_player(src_idx, 1, reason="卧薪尝胆反噬")

    add_log(f"🃏 【{src['name']}】打出 【{card}】" + (f" ➡️ 目标指向【{tgt['name']}】" if tgt else ""))

    if card == "回血":
        src['hp'] = min(src['max_hp'], src['hp'] + 1)
    elif card == "卡牌大师":
        draw_cards(src_idx, 2)
    elif card == "攻":
        set_attack_pipeline(src_idx, tgt_idx, "攻", 1)
    elif card == "荆轲刺秦":
        set_attack_pipeline(src_idx, tgt_idx, "荆轲刺秦", 2)
        damage_player(src_idx, 1, reason="荆轲刺秦反噬自损")
    elif card == "一字马":
        tgt['skipped'] = True
    elif card == "顺手牵羊":
        if tgt['hand']:
            stolen = random.choice(tgt['hand'])
            tgt['hand'].remove(stolen)
            src['hand'].append(stolen)
            add_log(f"🥷 成功顺走了【{tgt['name']}】的1张随机手牌！")
    elif card == "江山易主":
        src['hand'], tgt['hand'] = tgt['hand'], src['hand']
        add_log(f"🔄 双方手牌爆发大挪移全部对调！")
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
        add_log(f"🧱 人机【{tgt['name']}】直觉触发【长城】绝对屏蔽！")
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
    add_log(f"⚡ 【{p['name']}】装备了核心效果状态：【{status_card}】！")
    
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
        add_log(f"🩸 【{p['name']}】触发【背水一战】：自损 {sacrifice} 血，疯狂抓取 {draw_count} 张牌！")
        draw_cards(idx, draw_count)
    else:
        add_log(f"🛡️ 【{p['name']}】放弃自残，常规摸牌 1 张。")
        draw_cards(idx, 1)
        
    broadcast_state()
    trigger_bot_if_needed()

def damage_player(idx, amount, reason=""):
    if amount <= 0: return
    p = game.players[idx]
    p['hp'] -= amount
    add_log(f"💥 【{p['name']}】受到【{reason}】带来的 {amount} 点真实伤害！")
    
    if p['hp'] <= 0 and p['faction'] == "丁" and not p.get('has_revived', False):
        p['has_revived'] = True
        p['faction_revealed'] = True
        p['hp'] = 2
        p['max_hp'] = max(p['max_hp'], 2)
        add_log(f"🔥✨ 【丁】阵营觉醒！【{p['name']}】原地复活，血量回升至2并强补2张牌！")
        draw_cards(idx, 2)
        
    if p['hp'] <= 0:
        p['hp'] = 0
        p['alive'] = False
        p['faction_revealed'] = True
        add_log(f"💀🪦 战报：【{p['name']}】不幸阵亡！真实身份为：【{p['faction']}】")
        p['hand'] = []
        p['status_cards'] = []
        p['status'] = "正常"

def check_victory_conditions():
    if not game.active: return
    si_alive = any(p['alive'] for p in game.players if p['faction'] == "司")
    ji_alive = any(p['alive'] for p in game.players if p['faction'] == "冀")
    
    if not ji_alive:
        game.active = False
        add_log("🏆👑 【大局已定】弑主成功！谋逆反贼【司】夺取天下获得胜利！")
        return
        
    if not si_alive:
        game.active = False
        add_log("🏆🌟 【大局已定】帝星闪耀！护国联军【冀】与【丁】成功诛杀乱臣【司】！")
        return

# ==========================================
# 📡 Socket.IO 多端组队联机网关
# ==========================================
@socketio.on('join_game')
def on_join_game(data):
    """真人玩家加入大厅"""
    if game.active:
        emit('action_error', {'msg': '🚨 战局正在火热进行中！如果处于死锁卡机状态，请让房主直接点击“重置房间”清除。'})
        return
        
    sid = request.sid
    name = data.get('name', '').strip()
    if not name: name = f"真人_{random.randint(100,999)}"
    
    # 检查是否重复加入
    existing = next((p for p in game.players if p.get('sid') == sid), None)
    if existing:
        existing['name'] = name
    else:
        # 严格限制多真人博弈的总容量上限为 3
        if len([p for p in game.players if not p.get('is_bot')]) >= 3:
            emit('action_error', {'msg': '❌ 满员啦！一个战场最多容纳3个真人。'})
            return
            
        game.players.append({
            "sid": sid, "name": name, "is_bot": False, "alive": True, "hp": 3, "max_hp": 3,
            "faction": "隐藏", "faction_revealed": False, "status": "正常", "status_cooldown": 0,
            "hand": [], "status_cards": [], "beishui_decided": False, "skipped": False, "has_revived": False
        })
        
    broadcast_lobby()

@socketio.on('start_game')
def on_start_game():
    """开始对局：核心算法根据当前真人玩家数自动精细补齐三种模式"""
    if game.active: return
    
    humans = [p for p in game.players if not p.get('is_bot')]
    if not humans:
        emit('action_error', {'msg': '❌ 大厅里没有真人，无法开启试炼！'})
        return
        
    # 固化真人队列
    game.players = humans
    
    # 【核心逻辑】：根据真人数量自动智能补齐人机（确保满足 3人 纯真人、2+1、1+2 三种情况）
    needed_bots = 3 - len(game.players)
    bot_names = ["🤖 诸葛硅基", "🤖 曹操算法", "🤖 司马算力"]
    for i in range(needed_bots):
        game.players.append({
            "sid": f"bot_sid_{i}_{int(time.time())}", "name": bot_names[i], "is_bot": True, "alive": True,
            "hp": 3, "max_hp": 3, "faction": "隐藏", "faction_revealed": False, "status": "正常",
            "status_cooldown": 0, "hand": [], "status_cards": [], "beishui_decided": False, "skipped": False, "has_revived": False
        })
        
    # 为所有人生成战斗全局索引
    for idx, p in enumerate(game.players):
        p['idx'] = idx
        
    start_game_engine()

@socketio.on('play_card')
def on_play_card(data):
    if not game.active or game.pending_action: return
    p = get_player_by_sid(request.sid)
    if not p or game.current_idx != p['idx']:
        emit('action_error', {'msg': '🚨 冷静！当前不是你的回合，无权调兵！'})
        return
        
    card = data.get('card')
    tgt_idx = data.get('target', -1)
    intent = data.get('intent') 
    
    if p['status'] == "暗度陈仓" and intent == "攻" and card == "防":
        if "防" in p['hand']:
            p['hand'].remove("防")
            p['hand'].append("攻")
            card = "攻"

    success = execute_play_card(p['idx'], card, tgt_idx)
    if not success:
        emit('action_error', {'msg': '❌ 无法打出该手牌（检视行动力或卡牌数）'})
    else:
        trigger_bot_if_needed()

@socketio.on('equip_status')
def on_equip_status(data):
    if not game.active or game.pending_action: return
    p = get_player_by_sid(request.sid)
    if not p or game.current_idx != p['idx']: return
    
    card = data.get('card')
    if card not in p['status_cards'] or p['status_cooldown'] > 0:
        emit('action_error', {'msg': '🚨 状态环正处于冷却中或卡牌异常！'})
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
    
    if resp_type == '长城' and "长城" in p['hand']:
        p['hand'].remove("长城")
        add_log(f"🧱 【{p['name']}】祭出【长城】绝对格挡！")
        game.pending_action = None
    elif resp_type == '防' and "防" in p['hand']:
        p['hand'].remove("防")
        game.pending_action['required_defenses'] -= 1
        add_log(f"🛡️ 【{p['name']}】打出【防】进行闪避。")
    elif resp_type == '攻_as_防' and p['status'] == "暗度陈仓" and "攻" in p['hand']:
        p['hand'].remove("攻")
        game.pending_action['required_defenses'] -= 1
        add_log(f"🎭 【{p['name']}】发动【暗度陈仓】以【攻】作【防】抵御刺杀！")
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

@socketio.on('reset_game')
def on_reset_game():
    """最高权限：暴力对局熔断，清除死锁并遣散人机，保留大厅真人"""
    game.reset_game_only()
    # 强令全前端页面清空战斗态
    socketio.emit('force_reload_all')
    broadcast_lobby()

if __name__ == '__main__':
    socketio.run(app, debug=True, port=5000)
