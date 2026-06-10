import random
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.config['SECRET_KEY'] = 'sg_kill_ultimate_2026'
# 允许任何跨域连接，确保本地开发测试顺畅
socketio = SocketIO(app, cors_allowed_origins="*")

# ==========================================
# 🃏 核心游戏规则与全量卡牌数据中心
# ==========================================
BASIC_CARDS = (
    ["攻"] * 15 + ["防"] * 10 + ["长城"] * 4 + ["回血"] * 6 + 
    ["卡牌大师"] * 4 + ["荆轲刺秦"] * 3 + ["一字马"] * 3 + 
    ["顺手牵羊"] * 4 + ["江山易主"] * 2 + ["同归于尽"] * 3
)
STATUS_CARDS = ["背水一战", "饮鸩止渴", "卧薪尝胆", "暗度陈仓"]

class GameEngine:
    def __init__(self):
        self.reset()

    def reset(self):
        self.active = False
        self.players = []       # 动态玩家存储空间
        self.bot_count = 2      # 默认匹配人机配额
        self.current_idx = 0    # 当前出手权归属指针
        self.round = 1          # 局势大轮次计数
        self.actions_left = 0   # 当前行动方拥有的剩余可支配行动力
        self.deck = []          # 主基础摸牌堆
        self.status_deck = []   # 状态装备牌堆
        self.pending_action = None  # 核心攻防挂起结算句柄（用于锁死等待响应）
        self.logs = []          # 历史战局事件存盘

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
    """洗牌池全量重置重构"""
    game.deck = list(BASIC_CARDS)
    random.shuffle(game.deck)
    game.status_deck = list(STATUS_CARDS)
    random.shuffle(game.status_deck)

def draw_cards(player_idx, count):
    """绝对安全的玩家摸牌机制"""
    p = game.players[player_idx]
    drawn = []
    for _ in range(count):
        if not game.deck:
            rebuild_decks()
            add_log("🤹 [核心通知] 基本卡牌堆已被抽空，自动重混洗核心弃牌堆！")
        if game.deck:
            drawn.append(game.deck.pop(0))
    p['hand'].extend(drawn)

def broadcast_lobby():
    """广播同步大厅组队基础看板"""
    lobby_players = [{"name": p['name']} for p in game.players if not p['is_bot']]
    socketio.emit('lobby_update', {
        'count': len(lobby_players),
        'bot_count': game.bot_count,
        'players': [{"name": p['name']} for p in game.players]
    })

def broadcast_state():
    """阵营情报完全隔离的船新精修级状态同步"""
    if not game.active: return
    
    # 动态锚定当前唯一的真人玩家（用于定制化下发第一视角包，防止真人偷看AI底牌）
    human_idx = next((i for i, p in enumerate(game.players) if not p['is_bot']), 0)
    human_player = game.players[human_idx]
    
    client_players = []
    for p in game.players:
        # 严格的身份黑盒机制：只有自己、死者、或已经明牌的“丁”护卫能向全场展现真实阵营
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
        
    # 动态拦截并解析是否需要弹窗阻断人类玩家进行防御响应
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
    })

# ==========================================
# 🎮 核心战斗引擎流转
# ==========================================
def start_game_engine():
    game.active = True
    game.round = 1
    game.logs = []
    rebuild_decks()
    
    # 绝不重复分配：固定抽出 司(背叛者), 冀(主星), 丁(护卫)
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
            p['faction_revealed'] = True # 冀作为核心领袖，开局直接明牌
        else:
            p['max_hp'] = 3
            p['hp'] = 3
            p['faction_revealed'] = False
            
        p['hand'] = [game.deck.pop(0) for _ in range(4)]
        p['status_cards'] = [game.status_deck.pop(0) for _ in range(2)]

    lord_idx = next((i for i, p in enumerate(game.players) if p['faction'] == "冀"), 0)
    add_log("⚔️ —— 乱世逐鹿，三国kill 精修核心竞技场正式开辟！ ——")
    add_log("📜 终极天命：【冀】与【丁】结盟互保，【司】需只身击杀【冀】夺取天下！")
    
    start_turn(lord_idx)

def start_turn(idx):
    if not game.active: return
    p = game.players[idx]
    if not p['alive']:
        next_turn()
        return
        
    game.current_idx = idx
    game.actions_left = 2 # 每一个回合重置赋予 2 点行动力上限
    p['beishui_decided'] = False
    
    # 检测并清算【一字马】定身控制
    if p.get('skipped', False):
        p['skipped'] = False
        add_log(f"⏰ 【{p['name']}】遭受一字马封印，被强制剥夺本轮全部行动权！")
        next_turn()
        return

    # 检测并清算【饮鸩止渴】剧毒侵蚀
    if p['status'] == "饮鸩止渴":
        p['status_cooldown'] -= 1
        if p['status_cooldown'] <= 0:
            p['max_hp'] = max(1, p['max_hp'] - 2)
            p['hp'] = min(p['max_hp'], p['hp'] + 2)
            add_log(f"🧪 【{p['name']}】饮鸩止渴慢性毒发！生命上限缩减2，强行回血2点。")
            p['status_cooldown'] = 3 # 循环毒发步长
    elif p['status_cooldown'] > 0:
        p['status_cooldown'] -= 1
        if p['status_cooldown'] == 0:
            p['status'] = "正常"
            add_log(f"✨ 【{p['name']}】的装备状态效果届满，恢复如初。")

    add_log(f"🎬 —— 当前轮到【{p['name']}】开始进行军事决策 ——")
    
    # 状态牌优先切入决策
    if p['status'] == "背水一战":
        if p['is_bot']:
            handle_bot_beishui(idx)
        else:
            broadcast_state()
    else:
        draw_cards(idx, 1) # 正常摸牌阶段
        broadcast_state()
        trigger_bot_if_needed()

def end_turn_logic():
    if not game.active: return
    add_log(f"🏁 【{game.players[game.current_idx]['name']}】宣布鸣金收兵，移交出手权。")
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
            add_log("🤹 [时空重置] 已触发2轮交替节点，全场弃牌重新大洗牌！")
            
    start_turn(next_idx)

# ==========================================
# 🤖 核心安全网：人机自断尾智能思考大脑
# ==========================================
def trigger_bot_if_needed():
    """全自动轮询推进机，一旦侦测到当前是人机且战场无阻断，立刻驱动人机出牌"""
    if not game.active or game.pending_action: return
    
    curr = game.players[game.current_idx]
    if curr['alive'] and curr['is_bot']:
        if curr['status'] == "背水一战" and not curr['beishui_decided']:
            handle_bot_beishui(game.current_idx)
            return
        run_bot_active_move(game.current_idx)

def run_bot_active_move(bot_idx):
    p = game.players[bot_idx]
    
    # 🎯【核心修复哨兵 1】行动力耗尽，绝不粘滞卡死，秒速交权退出！
    if game.actions_left <= 0 or not p['alive']:
        end_turn_logic()
        return

    # 🤖 自动检索并挂载手头的闲置状态牌
    if p['status'] == "正常" and p['status_cards']:
        scard = p['status_cards'].pop(0)
        equip_status_logic(bot_idx, scard)
        broadcast_state()
        trigger_bot_if_needed()
        return

    # 🎯【核心修复哨兵 2】过滤纯防御被动牌，人机绝对不能在主动出牌阶段错甩“防/长城”
    active_cards = [c for c in p['hand'] if c not in ["防", "长城"]]
    
    if not active_cards:
        add_log(f"🤖 人机【{p['name']}】盘算后发现无任何可主动打出的手牌，优雅鸣金。")
        end_turn_logic()
        return

    # 决策优先级判定
    card = None
    if "回血" in active_cards and p['hp'] < p['max_hp']:
        card = "回血"
    else:
        playable = [c for c in active_cards if c != "回血"]
        if playable:
            card = playable[0]
            
    # 🎯【核心修复哨兵 3】虽然有“回血”但血量全满，属于无效牌，同样利落刹车退出！
    if not card:
        add_log(f"🤖 人机【{p['name']}】判定手牌无战略下发空间，放弃剩余行动。")
        end_turn_logic()
        return

    # 定向搜寻合法对线目标
    target_idx = -1
    for i, target in enumerate(game.players):
        if i != bot_idx and target['alive']:
            target_idx = i
            break

    if target_idx == -1:
        end_turn_logic()
        return

    # 咔嚓出牌
    success = execute_play_card(bot_idx, card, target_idx)
    if not success:
        end_turn_logic()
    else:
        # 如果打出的是功能牌/自打牌（没产生涉及真人的等待阻塞阻断），继续鞭策人机完成连招
        if not game.pending_action:
            trigger_bot_if_needed()

def handle_bot_beishui(idx):
    p = game.players[idx]
    # AI 聪明决策：只有健康生命层数大于 2 才敢玩命残血大抽牌
    sac = min(2, p['hp'] - 1) if p['hp'] > 2 else 0
    execute_beishui_decision(idx, sac)

def handle_bot_defense_response(bot_idx):
    """当人机在非自己回合遭遇真人突袭攻击时，进行毫秒级自动防御拆招"""
    if not game.pending_action: return
    p = game.players[bot_idx]
    
    while game.pending_action and game.pending_action['required_defenses'] > 0:
        # 1. 优先打出必杀技【长城】绝对防御
        if "长城" in p['hand']:
            p['hand'].remove("长城")
            add_log(f"🧱 机器人【{p['name']}】自动开启【长城壁垒】，绝对格挡！")
            game.pending_action = None
            break
            
        has_def = "防" in p['hand']
        has_cc_atk = (p['status'] == "暗度陈仓" and "攻" in p['hand'])
        
        if has_def:
            p['hand'].remove("防")
            game.pending_action['required_defenses'] -= 1
            add_log(f"🛡️ 机器人【{p['name']}】甩出【防】，闪避层数扣减。")
        elif has_cc_atk:
            p['hand'].remove("攻")
            game.pending_action['required_defenses'] -= 1
            add_log(f"🎭 机器人【{p['name']}】凭【暗度陈仓】指鹿为马，拿【攻】当【防】抵挡了攻势！")
        else:
            # 彻底宣告防守失败，承受痛击
            card_name = game.pending_action['card']
            dmg = 2 if card_name == "荆轲刺秦" else 1
            if p['status'] == "卧薪尝胆" and card_name == "攻":
                dmg = max(0, dmg - 1)
                add_log(f"🛡️ 【{p['name']}】触发卧薪尝胆，令本次【攻】的直接伤害降低1点。")
                
            damage_player(bot_idx, dmg, reason=card_name)
            game.pending_action = None
            break
            
    if game.pending_action and game.pending_action['required_defenses'] <= 0:
        add_log(f"💨 机器人【{p['name']}】使出浑身解数，成功消解本轮多段狂怒袭杀！")
        game.pending_action = None
        
    check_victory_conditions()
    broadcast_state()

# ==========================================
# ⚔️ 核心手牌结算规则厂
# ==========================================
def execute_play_card(src_idx, card, tgt_idx):
    src = game.players[src_idx]
    tgt = game.players[tgt_idx] if tgt_idx != -1 else None

    if game.actions_left <= 0 or card not in src['hand']: return False

    game.actions_left -= 1
    src['hand'].remove(card)

    # 卧薪尝胆被动反噬判定
    if src['status'] == "卧薪尝胆":
        damage_player(src_idx, 1, reason="卧薪尝胆主动出牌自噬")

    add_log(f"🃏 【{src['name']}】出牌：【{card}】" + (f" ➡️ 目标锁定【{tgt['name']}】" if tgt else ""))

    if card == "回血":
        src['hp'] = min(src['max_hp'], src['hp'] + 1)
        add_log(f"💖 【{src['name']}】生命微光闪烁，恢复了1点伤势。")
    elif card == "卡牌大师":
        draw_cards(src_idx, 2)
    elif card == "攻":
        set_attack_pipeline(src_idx, tgt_idx, "攻", 1)
    elif card == "荆轲刺秦":
        set_attack_pipeline(src_idx, tgt_idx, "荆轲刺秦", 2)
        damage_player(src_idx, 1, reason="荆轲刺秦刺客反噬自损")
    elif card == "一字马":
        tgt['skipped'] = True
        add_log(f"🕸️ 【{tgt['name']}】双脚离地被套上【一字马】定身咒，下轮无法动弹！")
    elif card == "顺手牵羊":
        if tgt['hand']:
            stolen = random.choice(tgt['hand'])
            tgt['hand'].remove(stolen)
            src['hand'].append(stolen)
            add_log(f"🥷 【{src['name']}】诡异探手，暗中顺走了【{tgt['name']}】的1张手牌底牌！")
        else:
            add_log(f"💨 【{tgt['name']}】两袖清风，【顺手牵羊】空手而归。")
    elif card == "江山易主":
        src['hand'], tgt['hand'] = tgt['hand'], src['hand']
        add_log(f"🔄 惊天剧变！【{src['name']}】与【{tgt['name']}】将全部手牌进行大挪移互换！")
    elif card == "同归于尽":
        damage_player(src_idx, 1, reason="同归于尽伤敌一千")
        damage_player(tgt_idx, 1, reason="同归于尽自损八百")
    
    check_victory_conditions()
    broadcast_state()
    return True

def set_attack_pipeline(src_idx, tgt_idx, card, count):
    """注入打击挂载管道"""
    tgt = game.players[tgt_idx]
    # 开局直接判断拦截机器人瞬发防守牌
    if "长城" in tgt['hand'] and tgt['is_bot']:
        tgt['hand'].remove("长城")
        add_log(f"🧱 机器人【{tgt['name']}】瞬发【长城】绝对屏蔽，伤害全免。")
        return

    game.pending_action = {
        "source_idx": src_idx,
        "target_idx": tgt_idx,
        "card": card,
        "required_defenses": count
    }
    
    if tgt['is_bot']:
        handle_bot_defense_response(tgt_idx)

def equip_status_logic(idx, status_card):
    p = game.players[idx]
    p['status'] = status_card
    add_log(f"⚡ 【{p['name']}】驱动装载了核心图鉴神技卡：【{status_card}】！")
    
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
        add_log(f"🩸 【{p['name']}】疯狂祭出【背水一战】：自残割裂 {sacrifice} 血，狂揽暴抽 {draw_count} 张新手牌！")
        draw_cards(idx, draw_count)
    else:
        add_log(f"🛡️ 【{p['name']}】在【背水一战】中选择常规平稳发育，常规摸牌 1 张。")
        draw_cards(idx, 1)
        
    broadcast_state()
    trigger_bot_if_needed()

def damage_player(idx, amount, reason=""):
    if amount <= 0: return
    p = game.players[idx]
    p['hp'] -= amount
    add_log(f"💥 【{p['name']}】因【{reason}】遭受了 {amount} 点致死级重创！")
    
    # 🛡️ 守护丁（丁）原地满血爆牌复活机制
    if p['hp'] <= 0 and p['faction'] == "丁" and not p.get('has_revived', False):
        p['has_revived'] = True
        p['faction_revealed'] = True
        p['hp'] = 2
        p['max_hp'] = max(p['max_hp'], 2)
        add_log(f"🔥✨ 【🛡️ 护卫不灭】玩家【{p['name']}】突破濒死线复苏！【丁】爆开真身，原地强行归位回血2层，并狂抽2张底牌！")
        draw_cards(idx, 2)
        
    if p['hp'] <= 0:
        p['hp'] = 0
        p['alive'] = False
        p['faction_revealed'] = True
        add_log(f"💀🪦 讣告：玩家【{p['name']}】支撑不住，宣布阵亡！临终公布真实内幕身份为：【{p['faction']}】")
        p['hand'] = []
        p['status_cards'] = []
        p['status'] = "正常"

def check_victory_conditions():
    """阵营绝对胜负清算器"""
    if not game.active: return
    si_alive = any(p['alive'] for p in game.players if p['faction'] == "司")
    ji_alive = any(p['alive'] for p in game.players if p['faction'] == "冀")
    
    if not ji_alive:
        game.active = False
        add_log("🏆👑 【大局已定】弑主反叛成功！乱臣【司】越塔击碎主星【冀】，夺得江山天下大胜！")
        broadcast_state()
        return
        
    if not si_alive:
        game.active = False
        add_log("🏆🌟 【大局已定】帝星万世长明！守护盟军【冀】与【丁】成功剿灭叛贼【司】，迎来破晓大胜！")
        broadcast_state()
        return

# ==========================================
# 📡 Socket.IO 前后端全网交互网关
# ==========================================
@socketio.on('change_bot_count')
def on_change_bot_count(data):
    if not game.active:
        game.bot_count = int(data.get('bot_count', 2))
        broadcast_lobby()

@socketio.on('join_game')
def on_join_game(data):
    if game.active:
        emit('action_error', {'msg': '战局正酣，请勿强行破门！如需重来请点击大厅顶部暴力重置。'})
        return
        
    name = data.get('name', '').strip()
    if not name: name = f"玩家_{random.randint(100,999)}"
    
    # 强制单真人速开沙盒过滤，防止由于多开导致的状态混乱
    game.players = [{
        "name": name, "idx": 0, "is_bot": False, "alive": True, "hp": 3, "max_hp": 3,
        "faction": "隐藏", "faction_revealed": False, "status": "正常", "status_cooldown": 0,
        "hand": [], "status_cards": [], "beishui_decided": False, "skipped": False, "has_revived": False
    }]
    
    # 自动根据玩家设置的人机数量补全队伍，保证任何时候点击“确认加入”都能直接开始3人激战！
    needed_bots = 3 - len(game.players)
    bot_pool = ["🤖 诸葛人机", "🤖 曹操硅基", "🤖 司马算力"]
    for i in range(needed_bots):
        game.players.append({
            "name": bot_pool[i % len(bot_pool)], "idx": len(game.players), "is_bot": True, "alive": True,
            "hp": 3, "max_hp": 3, "faction": "隐藏", "faction_revealed": False, "status": "正常",
            "status_cooldown": 0, "hand": [], "status_cards": [], "beishui_decided": False, "skipped": False, "has_revived": False
        })
        
    start_game_engine()

@socketio.on('play_card')
def on_play_card(data):
    if not game.active or game.pending_action: return
    card = data.get('card')
    tgt_idx = data.get('target', -1)
    intent = data.get('intent') # 捕获陈仓变形金刚意图
    
    human_idx = next((i for i, p in enumerate(game.players) if not p['is_bot']), 0)
    if game.current_idx != human_idx:
        emit('action_error', {'msg': '🚨 冷静！现在是机器人的出牌思考流转，请勿乱动！'})
        return
        
    p = game.players[human_idx]
    # 暗度陈仓奇袭技能代理
    if p['status'] == "暗度陈仓" and intent == "攻" and card == "防":
        if "防" in p['hand']:
            p['hand'].remove("防")
            p['hand'].append("攻")
            card = "攻"

    success = execute_play_card(human_idx, card, tgt_idx)
    if not success:
        emit('action_error', {'msg': '❌ 无法打出该手牌（可能是行动力耗尽或卡牌不合法）'})
    else:
        trigger_bot_if_needed()

@socketio.on('equip_status')
def on_equip_status(data):
    if not game.active or game.pending_action: return
    card = data.get('card')
    
    human_idx = next((i for i, p in enumerate(game.players) if not p['is_bot']), 0)
    if game.current_idx != human_idx: return
    
    p = game.players[human_idx]
    if card not in p['status_cards'] or p['status_cooldown'] > 0:
        emit('action_error', {'msg': '🚨 该状态卡环正处于CD或不属于你！'})
        return
        
    p['status_cards'].remove(card)
    equip_status_logic(human_idx, card)
    broadcast_state()
    trigger_bot_if_needed()

@socketio.on('respond_action')
def on_respond_action(data):
    """当真人遭到人机攻击，在屏幕中央弹窗点击抗击时的处理插座"""
    if not game.active or not game.pending_action: return
    resp_type = data.get('type') # '防', '攻_as_防', '长城', '放弃'
    tgt_idx = game.pending_action['target_idx']
    p = game.players[tgt_idx]
    card_name = game.pending_action['card']
    
    if resp_type == '长城' and "长城" in p['hand']:
        p['hand'].remove("长城")
        add_log(f"🧱 【{p['name']}】祭出【长城】绝对防御，物理偏转本次袭击！")
        game.pending_action = None
    elif resp_type == '防' and "防" in p['hand']:
        p['hand'].remove("防")
        game.pending_action['required_defenses'] -= 1
        add_log(f"🛡️ 【{p['name']}】消耗1张【防】完成一次招架。")
    elif resp_type == '攻_as_防' and p['status'] == "暗度陈仓" and "攻" in p['hand']:
        p['hand'].remove("攻")
        game.pending_action['required_defenses'] -= 1
        add_log(f"🎭 【{p['name']}】发动【暗度陈仓】，横刀将【攻】作【防】抗下了这一击！")
    elif resp_type == '放弃':
        dmg = 2 if card_name == "荆轲刺秦" else 1
        if p['status'] == "卧薪尝胆" and card_name == "攻":
            dmg = max(0, dmg - 1)
        damage_player(tgt_idx, dmg, reason=card_name)
        game.pending_action = None
        
    if game.pending_action and game.pending_action['required_defenses'] <= 0:
        add_log(f"✨ 【{p['name']}】闪避护甲拉满，完好无损地挡下了全套重击。")
        game.pending_action = None

    check_victory_conditions()
    broadcast_state()
    # 真人防御判定结束后，若现在仍为人机回合，驱动人机继续进行接下来的连招
    trigger_bot_if_needed()

@socketio.on('beishui_decision')
def on_beishui_decision(data):
    if not game.active: return
    sacrifice = int(data.get('sacrifice', 0))
    human_idx = next((i for i, p in enumerate(game.players) if not p['is_bot']), 0)
    
    if game.current_idx != human_idx: return
    p = game.players[human_idx]
    if p['status'] != "背水一战" or p['beishui_decided']: return
    
    execute_beishui_decision(human_idx, sacrifice)

@socketio.on('end_turn')
def on_end_turn():
    human_idx = next((i for i, p in enumerate(game.players) if not p['is_bot']), 0)
    if game.current_idx != human_idx: return
    end_turn_logic()

@socketio.on('reset_game')
def on_reset_game():
    game.reset()
    add_log("🔄 房主执行了最高级别的暴力重置房间代码，网页即将全面强制冲洗刷新！")
    socketio.emit('force_reload_all')

if __name__ == '__main__':
    # 绑定 5000 端口，开启 Debug 高速自热重载
    socketio.run(app, debug=True, port=5000)
