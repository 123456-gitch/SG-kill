import os
import random
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.config['SECRET_KEY'] = 'sg_kill_human_perfect_edition_2026_with_bots'
socketio = SocketIO(app, cors_allowed_origins="*", logger=False, engineio_logger=False, async_mode='gevent')

BASIC_CARDS = (
    ["攻"] * 50 + ["防"] * 45 + ["长城"] * 20 + ["回血"] * 25 +
    ["卡牌大师"] * 20 + ["荆轲刺秦"] * 8 + ["一字马"] * 8 +
    ["顺手牵羊"] * 15 + ["江山易主"] * 4 + ["同归于尽"] * 5
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
            add_log("🤹 [洗牌] 牌堆已空，重新混洗基本牌堆！")
        if game.deck:
            drawn.append(game.deck.pop(0))
    p['hand'].extend(drawn)

def force_hp_limit(player):
    if player['hp'] > player['max_hp']:
        player['hp'] = player['max_hp']

def broadcast_lobby():
    humans = [p for p in game.players if not p.get('is_bot', False)]
    socketio.emit('lobby_update', {
        'count': len(humans),
        'players': [{"name": p['name']} for p in humans],
        'game_active': game.active,
        'bot_count': game.bot_count
    })

def broadcast_state():
    if not game.active: return
    for human_player in game.players:
        if human_player.get('is_bot', False): continue
        human_idx = human_player['idx']
        client_players = []
        for p in game.players:
            visible_faction = "隐藏"
            if p['idx'] == human_idx:
                visible_faction = p['faction']
            elif not p['alive']:
                visible_faction = p['faction']
            elif p.get('faction_revealed', False):
                visible_faction = p['faction']
            client_players.append({
                "name": p['name'], "idx": p['idx'], "alive": p['alive'],
                "hp": p['hp'], "max_hp": p['max_hp'], "faction": visible_faction,
                "hand_count": len(p['hand']), "status": p['status'],
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
            "round": game.round, "current_idx": game.current_idx, "actions_left": game.actions_left,
            "deck_count": len(game.deck), "my_idx": human_idx, "players": client_players,
            "my_cards": human_player['hand'], "my_status_cards": human_player['status_cards'],
            "my_status": human_player['status'], "is_my_response": is_my_response,
            "pending_card": pending_card, "required_defenses": required_defenses,
            "is_beishui_prompt": is_beishui_prompt
        }, to=human_player['sid'])

def get_player_by_sid(sid):
    return next((p for p in game.players if p.get('sid') == sid), None)

def start_game_engine():
    game.active = True
    game.round = 1
    game.new_round_started = True
    game.logs = []
    rebuild_decks()
    factions = ["司", "冀", "丁"]
    random.shuffle(factions)
    for i, p in enumerate(game.players):
        p['idx'] = i
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
        all_status = list(STATUS_CARDS)
        random.shuffle(all_status)
        p['status_cards'] = all_status[:2]
    first_idx = random.randint(0, 2)
    start_turn(first_idx)

def start_turn(idx):
    if not game.active: return
    p = game.players[idx]
    if not p['alive']:
        next_turn()
        return
    for player in game.players:
        if player['alive'] and player['status'] == "饮鸩止渴":
            player['max_hp'] = max(1, player['max_hp'] - 3)
            force_hp_limit(player)
            add_log(f"🧪 【{player['name']}】饮鸩止渴毒发：上限-3 → 当前 {player['hp']}/{player['max_hp']}")
    game.current_idx = idx
    game.actions_left = game.round + 1
    p['beishui_decided'] = False
    all_status = list(STATUS_CARDS)
    random.shuffle(all_status)
    p['status_cards'] = all_status[:2]
    if p.get('skipped', False):
        p['skipped'] = False
        add_log(f"⏰ 【{p['name']}】身上【一字马】咒术生效，跳过本回合行动权！")
        next_turn()
        return
    add_log(f"🎬 ====== 【{p['name']}】的回合开始 ======")
    broadcast_state()
    trigger_bot_if_needed()

def end_turn_logic():
    if not game.active: return
    add_log(f"🏁 【{game.players[game.current_idx]['name']}】回合宣告结束")
    for p in game.players:
        if p['alive'] and p['status_cooldown'] > 0:
            p['status_cooldown'] -= 1
            if p['status_cooldown'] == 0:
                add_log(f"✨ 【{p['name']}】的【{p['status']}】冷却结束，现在可以更换了！")
    next_turn()

def next_turn():
    if not game.active: return
    attempts = 0
    next_idx = game.current_idx
    while attempts < 4:
        next_idx = (next_idx + 1) % len(game.players)
        if game.players[next_idx]['alive']: break
        attempts += 1
    if next_idx == game.current_idx: return
    first_player_idx = next((i for i, p in enumerate(game.players) if p['alive']), 0)
    if next_idx == first_player_idx:
        game.round += 1
        add_log(f"📢 ====== 第 {game.round} 轮战斗拉开帷幕 ======")
        for player in game.players:
            if player['alive']:
                cards_needed = 5 - len(player['hand'])
                if cards_needed > 0:
                    draw_cards(player['idx'], cards_needed)
                    add_log(f"✋ 【{player['name']}】大轮次自动补充手牌 {cards_needed} 张")
        if game.round % 2 == 1: rebuild_decks()
    start_turn(next_idx)

def trigger_bot_if_needed():
    if not game.active or game.pending_action: return
    curr = game.players[game.current_idx]
    if curr['alive'] and curr.get('is_bot', False):
        socketio.start_background_task(run_bot_turn, game.current_idx)

def check_actions_and_end_turn():
    if not game.active or game.pending_action: return
    if game.actions_left <= 0:
        add_log(f"⚠️ 【{game.players[game.current_idx]['name']}】行动力耗尽！")
        end_turn_logic()
    else:
        trigger_bot_if_needed()

def get_bot_playable_cards(bot_idx):
    p = game.players[bot_idx]
    playable = []
    for card in p['hand']:
        if card == "防":
            if p['status'] == "暗度陈仓": playable.append(("防", "攻"))
        elif card == "长城": pass
        elif card == "回血":
            if p['hp'] < p['max_hp']: playable.append(("回血", "回血"))
        else:
            playable.append((card, card))
    return playable

def get_bot_target(bot_idx):
    bot = game.players[bot_idx]
    others = [p for p in game.players if p['idx'] != bot_idx and p['alive']]
    if not others: return -1
    revealed_enemies = []
    for p in others:
        is_enemy = False
        if bot['faction'] == "司": is_enemy = p['faction'] in ["冀", "丁"]
        else: is_enemy = p['faction'] == "司"
        if is_enemy and p.get('faction_revealed', False): revealed_enemies.append(p)
    if revealed_enemies: return random.choice(revealed_enemies)['idx']
    return random.choice(others)['idx']

def run_bot_turn(bot_idx):
    socketio.sleep(2)
    if not game.active or game.current_idx != bot_idx or game.pending_action: return
    p = game.players[bot_idx]
    if p['status'] == "正常" or p['status_cooldown'] == 0:
        if p['status_cards']:
            scard = random.choice(p['status_cards'])
            p['status_cards'].remove(scard)
            equip_status_logic(bot_idx, scard)
            broadcast_state()
            socketio.sleep(2)
    p = game.players[bot_idx]
    if game.active and p['status'] == "背水一战" and not p['beishui_decided']:
        sacrifice = min(p['hp'] - 1, 3) if p['hp'] > 1 else 0
        execute_beishui_decision(bot_idx, sacrifice)
        broadcast_state()
        socketio.sleep(2)
    while game.active and game.current_idx == bot_idx and game.actions_left > 0 and not game.pending_action:
        playable = get_bot_playable_cards(bot_idx)
        if not playable: break
        attacks = [item for item in playable if item[1] == "攻"]
        other_attacks = [item for item in playable if item[1] == "荆轲刺秦"]
        others = [item for item in playable if item[1] not in ["攻", "荆轲刺秦"]]
        if attacks: spend, execute = random.choice(attacks)
        elif other_attacks: spend, execute = random.choice(other_attacks)
        else: spend, execute = random.choice(others)
        if execute in ["回血", "卡牌大师"]: tgt_idx = -1
        else:
            tgt_idx = get_bot_target(bot_idx)
            if tgt_idx == -1: break
        success = execute_play_card(bot_idx, spend, execute, tgt_idx)
        if not success: break
        socketio.sleep(2)
    if game.active and game.current_idx == bot_idx and not game.pending_action:
        end_turn_logic()

def run_bot_defense(bot_idx):
    socketio.sleep(2)
    if not game.active or not game.pending_action: return
    if game.pending_action['target_idx'] != bot_idx: return
    p = game.players[bot_idx]
    card_name = game.pending_action['card']
    if card_name in ["攻", "荆轲刺秦"]:
        if "防" in p['hand']: execute_defense_response(bot_idx, '防')
        elif p['status'] == "暗度陈仓" and "攻" in p['hand']: execute_defense_response(bot_idx, '攻_as_防')
        elif "长城" in p['hand']: execute_defense_response(bot_idx, '长城')
        else: execute_defense_response(bot_idx, '放弃')
    else:
        if "长城" in p['hand']: execute_defense_response(bot_idx, '长城')
        else: execute_defense_response(bot_idx, '放弃')

def execute_defense_response(idx, resp_type):
    if not game.active or not game.pending_action: return
    p = game.players[idx]
    card_name = game.pending_action['card']
    src_idx = game.pending_action['source_idx']
    tgt_idx = game.pending_action['target_idx']
    was_pending = True
    blocked_by_greatwall = False
    if resp_type == '长城' and "长城" in p['hand']:
        p['hand'].remove("长城")
        add_log(f"🧱 机器人【{p['name']}】祭出高耸【长城】！完美格挡了本次针对其发动的【{card_name}】效果！")
        blocked_by_greatwall = True
        game.pending_action = None
    elif resp_type == '防' and "防" in p['hand'] and card_name in ["攻", "荆轲刺秦"]:
        p['hand'].remove("防")
        game.pending_action['required_defenses'] -= 1
        add_log(f"🛡️ 机器人【{p['name']}】打出【防】格挡（还需 {game.pending_action['required_defenses']} 张防）")
        if game.pending_action['required_defenses'] <= 0:
            add_log(f"✅ 机器人战术闪避成功！不受伤害。")
            game.pending_action = None
    elif resp_type == '攻_as_防' and p['status'] == "暗度陈仓" and "攻" in p['hand'] and card_name in ["攻", "荆轲刺秦"]:
        p['hand'].remove("攻")
        game.pending_action['required_defenses'] -= 1
        add_log(f"🎭 机器人【{p['name']}】暗度陈仓以【攻】代【防】！（还需 {game.pending_action['required_defenses']} 张防）")
        if game.pending_action['required_defenses'] <= 0:
            add_log(f"✅ 机器人战术变招防御成功！")
            game.pending_action = None
    elif resp_type == '放弃':
        execute_card_effect(src_idx, tgt_idx, card_name)
        game.pending_action = None
        src_player = game.players[src_idx]
        if not src_player['alive'] and game.current_idx == src_idx and game.active:
            add_log(f"⏭️ 回合主行动方【{src_player['name']}】阵亡，出牌阶段强制终止。")
            next_turn()
            return
    # 修复：长城完全废掉整张牌，荆轲刺秦的自损反噬也不触发
    if was_pending and not game.pending_action and card_name == "荆轲刺秦" and not blocked_by_greatwall and game.active:
        src_player = game.players[src_idx]
        if src_player['alive']:
            damage_player(src_idx, 1, "荆轲刺秦反噬自损")
            add_log(f"🗡️ 【{src_player['name']}】荆轲刺秦反噬：自损1点体力！")
    check_victory_conditions()
    # 修复：防御结算后如果当前回合玩家死亡，自动结束回合（避免僵局）
    if game.active and not game.players[game.current_idx]['alive'] and not game.pending_action:
        add_log(f"⏭️ 回合主行动方【{game.players[game.current_idx]['name']}】阵亡，出牌阶段强制终止。")
        next_turn()
        return
    broadcast_state()
    if game.active and game.pending_action and game.pending_action['target_idx'] == idx:
        socketio.start_background_task(run_bot_defense, idx)
    else:
        check_actions_and_end_turn()

def execute_play_card(src_idx, card_to_spend, card_to_execute, tgt_idx):
    src = game.players[src_idx]
    tgt = game.players[tgt_idx] if tgt_idx != -1 else None
    if game.actions_left <= 0: return False
    if card_to_spend not in src['hand']: return False
    game.actions_left -= 1
    src['hand'].remove(card_to_spend)
    if src['status'] == "卧薪尝胆" and card_to_execute != "回血":
        if src['hp'] > 1:
            src['hp'] -= 1
            add_log(f"🔥 【{src['name']}】卧薪尝胆反噬，自损1血！(剩余:{src['hp']}/{src['max_hp']})")
        else:
            add_log(f"🔥 【{src['name']}】卧薪尝胆反噬，但血量已为保底1血，不触发致死。")
    if card_to_execute in ["回血", "卡牌大师"]:
        add_log(f"🃏 【{src['name']}】打出了【{card_to_execute}】")
    else:
        add_log(f"🃏 【{src['name']}】打出了【{card_to_execute}】 🎯 瞄准目标 → 【{tgt['name']}】")
    if card_to_execute == "回血":
        if src['hp'] < src['max_hp']:
            src['hp'] += 1
            add_log(f"💚 生命恢复 +1 → {src['hp']}/{src['max_hp']}")
        else:
            add_log(f"💚 已满生命值上限，无实际效果")
        force_hp_limit(src)
    elif card_to_execute == "卡牌大师":
        draw_cards(src_idx, 2)
    elif card_to_execute == "荆轲刺秦":
        set_attack_pipeline(src_idx, tgt_idx, "荆轲刺秦", 2)
    else:
        set_attack_pipeline(src_idx, tgt_idx, card_to_execute, 1)
    if not src['alive']:
        add_log(f"⏭️ 回合主行动方【{src['name']}】阵亡，出牌阶段强制终止。")
        next_turn()
        return True
    if game.actions_left <= 0 and not game.pending_action:
        add_log(f"⚠️ 【{src['name']}】行动力耗尽！")
        end_turn_logic()
        return True
    check_victory_conditions()
    broadcast_state()
    return True

def set_attack_pipeline(src_idx, tgt_idx, card, count):
    game.pending_action = {"source_idx": src_idx, "target_idx": tgt_idx, "card": card, "required_defenses": count}
    tgt = game.players[tgt_idx]
    if tgt.get('is_bot', False):
        socketio.start_background_task(run_bot_defense, tgt_idx)

def execute_card_effect(src_idx, tgt_idx, card):
    src = game.players[src_idx]
    tgt = game.players[tgt_idx]
    if card == "攻":
        dmg = 1
        if tgt['status'] == "卧薪尝胆":
            dmg = max(0, dmg - 1)
            add_log(f"🛡️ 【{tgt['name']}】卧薪尝胆被动减伤：本次受到的【攻】伤害归零！")
        if dmg > 0:
            damage_player(tgt_idx, dmg, "攻")
    elif card == "荆轲刺秦":
        dmg = game.pending_action['required_defenses'] if game.pending_action else 2
        if tgt['status'] == "卧薪尝胆":
            dmg = max(0, dmg - 1)
            add_log(f"🛡️ 【{tgt['name']}】卧薪尝胆被动减伤：本次受到的【荆轲刺秦】伤害减少1点！")
        if dmg > 0:
            damage_player(tgt_idx, dmg, "荆轲刺秦")
    elif card == "一字马":
        tgt['skipped'] = True
        add_log(f"🔒 【{tgt['name']}】被一字马咒术封锁！下轮行动跳过")
    elif card == "顺手牵羊":
        if tgt['hand']:
            stolen = random.choice(tgt['hand'])
            tgt['hand'].remove(stolen)
            src['hand'].append(stolen)
            add_log(f"🥷 【{src['name']}】顺走【{tgt['name']}】1张暗牌")
        else:
            add_log(f"🥷 目标手牌为空，【顺手牵羊】空手而返")
    elif card == "江山易主":
        src['hand'], tgt['hand'] = tgt['hand'], src['hand']
        add_log(f"🔄 【{src['name']}】与【{tgt['name']}】大对调手牌！")
    elif card == "同归于尽":
        add_log(f"💥 彼此受到自爆，生命各减1点")
        damage_player(src_idx, 1, "同归于尽自损")
        damage_player(tgt_idx, 1, "同归于尽重创")

def equip_status_logic(idx, status_card):
    p = game.players[idx]
    p['status'] = status_card
    p['status_cooldown'] = 3
    add_log(f"⚡ 【{p['name']}】武装状态牌 → 【{status_card}】(CD 3回合)")
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
        if sacrifice > 0:
            p['hp'] -= sacrifice
            draw_count = sacrifice + 1
            add_log(f"🩸 【{p['name']}】背水一战：自残生命 {sacrifice} 点，爆抽 {draw_count} 张手牌！")
            draw_cards(idx, draw_count)
        else:
            add_log(f"🩸 【{p['name']}】已为最低1血，无法献祭自损，改进行常规补满5张")
            cards_needed = 5 - len(p['hand'])
            if cards_needed > 0: draw_cards(idx, cards_needed)
    else:
        cards_needed = 5 - len(p['hand'])
        if cards_needed > 0: draw_cards(idx, cards_needed)
        add_log(f"🛡️ 【{p['name']}】选择稳健，不进行自损，常规补牌至 5 张")
    broadcast_state()

def damage_player(idx, amount, reason=""):
    if amount <= 0: return
    p = game.players[idx]
    p['hp'] -= amount
    add_log(f"💥 【{p['name']}】因[{reason}]失去 {amount} 点体力 → 生命值当前为 {p['hp']}/{p['max_hp']}")
    if p['hp'] <= 0 and p['faction'] == "丁" and not p.get('has_revived', False):
        p['has_revived'] = True
        p['max_hp'] = max(p['max_hp'], 2)
        p['hp'] = p['max_hp']
        p['faction_revealed'] = True
        for all_p in game.players:
            all_p['faction_revealed'] = True
        add_log(f"🔥✨ 守护神【丁】血限归零！原地复活！生命值回满并补充 3 张牌，全场身份自此大白于天下！")
        draw_cards(idx, 3)
        for other_p in game.players:
            if other_p['alive'] and other_p['faction'] == "司":
                add_log(f"👑🌟 身份公开警报已拉响！叛逆者【{other_p['name']}】(司) 受到命运眷顾进入无双状态，强制爆抽 10 张基本手牌！")
                draw_cards(other_p['idx'], 10)
    if p['hp'] <= 0:
        p['hp'] = 0
        p['alive'] = False
        p['faction_revealed'] = True
        add_log(f"💀🪦 【{p['name']}】力战阵亡！其身份最终揭开：【{p['faction']}】")
        p['hand'] = []
        p['status_cards'] = []
        p['status'] = "正常"

def check_victory_conditions():
    if not game.active: return
    si_alive = any(p['alive'] for p in game.players if p['faction'] == "司")
    ji_alive = any(p['alive'] for p in game.players if p['faction'] == "冀")
    if not ji_alive:
        game.active = False
        add_log("🏆👑 【司】胜利！主星【冀】已遭到灭杀！")
        socketio.emit('game_over', {
            "winner": "司 (叛逆者)",
            "msg": "【司】成功击杀主星【冀】，击溃了【冀+丁】联盟，获得独立决战的最终胜利！"
        })
        return
    if not si_alive:
        game.active = False
        add_log("🏆🌟 【冀+丁】盟友阵营大捷！叛逆者【司】已经被全部清除！")
        socketio.emit('game_over', {
            "winner": "冀 + 丁 (守护联盟)",
            "msg": "主星【冀】与护卫【丁】成功将【司】绳之以法，完美守护了和平！"
        })
        return

@socketio.on('connect')
def on_connect():
    broadcast_lobby()

@socketio.on('change_bot_count')
def on_change_bot_count(data):
    if not game.active:
        game.bot_count = min(2, max(0, int(data.get('bot_count', 2))))
        required_humans = 3 - game.bot_count
        game.players = game.players[:required_humans]
        broadcast_lobby()

@socketio.on('join_game')
def on_join_game(data):
    sid = request.sid
    name = data.get('name', '').strip()
    if not name: name = f"玩家_{random.randint(100, 999)}"
    if game.active:
        existing = next((p for p in game.players if p['name'] == name), None)
        if existing:
            existing['sid'] = sid
            add_log(f"🔄 玩家【{name}】重连联机成功！")
            broadcast_state()
            return
        else:
            emit('action_error', {'msg': '🚨 对战正在热血搏弈中！且大厅没有你的名字，无法中途加入。'})
            return
    game.players = [p for p in game.players if not p.get('is_bot', False)]
    existing_lobby = next((p for p in game.players if p['name'] == name), None)
    if existing_lobby:
        existing_lobby['sid'] = sid
    else:
        required_humans = 3 - game.bot_count
        if len(game.players) >= required_humans:
            emit('action_error', {'msg': '🚨 决战人数已满，游戏即将开始！'})
            return
        game.players.append({
            "sid": sid, "name": name, "alive": True, "hp": 5, "max_hp": 5,
            "faction": "隐藏", "status": "正常", "status_cooldown": 0,
            "hand": [], "status_cards": [], "beishui_decided": False, "skipped": False,
            "has_revived": False, "faction_revealed": False, "is_bot": False
        })
    required_humans = 3 - game.bot_count
    current_humans = len([p for p in game.players if not p.get('is_bot', False)])
    if current_humans >= required_humans:
        bot_names = ["🤖 诸葛算法", "🤖 曹操芯片", "🤖 司马算力"]
        for i in range(game.bot_count):
            game.players.append({
                "sid": f"bot_{i}", "name": bot_names[i], "alive": True, "hp": 5, "max_hp": 5,
                "faction": "隐藏", "status": "正常", "status_cooldown": 0,
                "hand": [], "status_cards": [], "beishui_decided": False, "skipped": False,
                "has_revived": False, "faction_revealed": False, "is_bot": True
            })
        for idx, p in enumerate(game.players): p['idx'] = idx
        start_game_engine()
    else:
        broadcast_lobby()

@socketio.on('disconnect')
def on_disconnect():
    if not game.active:
        game.players = [p for p in game.players if p.get('sid') != request.sid]
        broadcast_lobby()

@socketio.on('reset_game')
def on_reset_game():
    game.reset_all()
    socketio.emit('force_reload_all')

@socketio.on('play_card')
def on_play_card(data):
    if not game.active: return
    p = get_player_by_sid(request.sid)
    if not p: return
    if game.pending_action:
        emit('action_error', {'msg': '🚨 决算危机结算中！请另一方先处理当前的防御响应'})
        return
    if game.current_idx != p['idx']:
        emit('action_error', {'msg': '🚨 现在是对方的回合！'})
        return
    card = data.get('card')
    tgt_idx = int(data.get('target', -1))
    intent = data.get('intent')
    if card in ["防", "长城"]:
        if p['status'] == "暗度陈仓" and card == "防" and intent == "攻":
            pass
        else:
            emit('action_error', {'msg': '🚨 被动闪避防御牌不能在自己回合主动直接出丢弃！'})
            return
    TARGET_CARDS = ["攻", "荆轲刺秦", "一字马", "顺手牵羊", "江山易主", "同归于尽"]
    if card in TARGET_CARDS and tgt_idx == -1:
        emit('action_error', {'msg': '🚨 请在顶部先选择一个其他活着的玩家瞄准锁定！'})
        return
    if tgt_idx != -1 and not game.players[tgt_idx]['alive']:
        emit('action_error', {'msg': '🚨 目标已经是阵亡状态！'})
        return
    if tgt_idx == p['idx']:
        emit('action_error', {'msg': '🚨 不能锁定你自己！'})
        return
    if game.actions_left <= 0:
        emit('action_error', {'msg': '🚨 本回合剩余行动力已经枯竭！'})
        return
    if p['status'] == "暗度陈仓" and card == "防" and intent == "攻":
        add_log(f"🎭 【{p['name']}】暗度陈仓：将手牌中的【防】当做【攻】打出！")
        execute_play_card(p['idx'], "防", "攻", tgt_idx)
    else:
        execute_play_card(p['idx'], card, card, tgt_idx)
    if game.active and not game.pending_action:
        check_actions_and_end_turn()

@socketio.on('equip_status')
def on_equip_status(data):
    if not game.active: return
    p = get_player_by_sid(request.sid)
    if not p: return
    if game.pending_action:
        emit('action_error', {'msg': '🚨 正处于防御博弈响应阶段，不可武装装备！'})
        return
    if game.current_idx != p['idx']:
        emit('action_error', {'msg': '🚨 只能在自己的行动回合内穿戴装备状态牌！'})
        return
    card = data.get('card')
    if card not in p['status_cards']:
        emit('action_error', {'msg': '🚨 卡牌图鉴未搜寻到该选择！'})
        return
    if p['status'] != "正常" and p['status_cooldown'] > 0:
        emit('action_error', {'msg': f'🚨 身上武装【{p["status"]}】还有 {p["status_cooldown"]} 回合冷却，无法脱卸更换！'})
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
    was_pending = True
    blocked_by_greatwall = False
    if resp_type == '长城' and "长城" in p['hand']:
        p['hand'].remove("长城")
        add_log(f"🧱 【{p['name']}】祭出高耸【长城壁】！完美格挡了本次针对其发动的【{card_name}】效果！")
        blocked_by_greatwall = True
        game.pending_action = None
    elif resp_type == '防' and "防" in p['hand'] and card_name in ["攻", "荆轲刺秦"]:
        p['hand'].remove("防")
        game.pending_action['required_defenses'] -= 1
        add_log(f"🛡️ 【{p['name']}】打出【防】格挡（还需 {game.pending_action['required_defenses']} 张防）")
        if game.pending_action['required_defenses'] <= 0:
            add_log(f"✅ 战术闪避成功！不受伤害。")
            game.pending_action = None
    elif resp_type == '攻_as_防' and p['status'] == "暗度陈仓" and "攻" in p['hand'] and card_name in ["攻", "荆轲刺秦"]:
        p['hand'].remove("攻")
        game.pending_action['required_defenses'] -= 1
        add_log(f"🎭 【{p['name']}】暗度陈仓以【攻】代【防】！（还需 {game.pending_action['required_defenses']} 张防）")
        if game.pending_action['required_defenses'] <= 0:
            add_log(f"✅ 战术变招防御成功！")
            game.pending_action = None
    elif resp_type == '放弃':
        execute_card_effect(src_idx, tgt_idx, card_name)
        game.pending_action = None
        src_player = game.players[src_idx]
        if not src_player['alive'] and game.current_idx == src_idx and game.active:
            add_log(f"⏭️ 回合主行动方【{src_player['name']}】阵亡，出牌阶段强制终止。")
            next_turn()
            return
    # 修复：长城完全废掉整张牌，荆轲刺秦的自损反噬也不触发
    if was_pending and not game.pending_action and card_name == "荆轲刺秦" and not blocked_by_greatwall and game.active:
        src_player = game.players[src_idx]
        if src_player['alive']:
            damage_player(src_idx, 1, "荆轲刺秦反噬自损")
            add_log(f"🗡️ 【{src_player['name']}】荆轲刺秦反噬：自损1点体力！")
    check_victory_conditions()
    # 修复：防御结算后如果当前回合玩家死亡，自动结束回合（避免僵局）
    if game.active and not game.players[game.current_idx]['alive'] and not game.pending_action:
        add_log(f"⏭️ 回合主行动方【{game.players[game.current_idx]['name']}】阵亡，出牌阶段强制终止。")
        next_turn()
        return
    broadcast_state()
    if game.active and not game.pending_action:
        check_actions_and_end_turn()

@socketio.on('beishui_decision')
def on_beishui_decision(data):
    if not game.active: return
    p = get_player_by_sid(request.sid)
    if not p or game.current_idx != p['idx']: return
    if p['status'] != "背水一战" or p['beishui_decided']: return
    sacrifice = int(data.get('sacrifice', 0))
    execute_beishui_decision(p['idx'], sacrifice)
    trigger_bot_if_needed()

@socketio.on('end_turn')
def on_end_turn():
    p = get_player_by_sid(request.sid)
    if not p or game.current_idx != p['idx']: return
    if game.pending_action:
        emit('action_error', {'msg': '🚨 还有未结算的攻击，无法结束回合！'})
        return
    end_turn_logic()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, debug=False, host='0.0.0.0', port=port)
