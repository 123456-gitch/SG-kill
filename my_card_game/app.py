import random
import time
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit

print("[系统通知] 三国kill (阵营天眼进化版) 服务端核心已启动...")

app = Flask(__name__)
app.config['SECRET_KEY'] = 'three_kingdoms_kill_v5_intel'
socketio = SocketIO(app, cors_allowed_origins="*")

game_state = {
    "players": [],       
    "deck": [],          
    "discard_pile": [],  
    "round": 1,          
    "current_idx": 0,    
    "actions_left": 2,   
    "game_started": False,
    "pending_response": None,
    "pending_beishui": None,
    "bot_count": 0,        
    "bot_running": False   
}

BASIC_POOL = (
    ["攻"] * 60 + ["防"] * 50 + ["长城"] * 20 + ["回血"] * 20 +
    ["荆轲刺秦"] * 10 + ["一字马"] * 10 + ["顺手牵羊"] * 10 + ["江山易主"] * 5 +
    ["卡牌大师"] * 10 + ["同归于尽"] * 5
)

STATUS_POOL = ["背水一战", "饮鸩止渴", "卧薪尝胆", "暗度陈仓"]

def init_game():
    game_state["deck"] = BASIC_POOL.copy()
    random.shuffle(game_state["deck"])
    game_state["discard_pile"] = []
    game_state["round"] = 1
    game_state["current_idx"] = 0
    game_state["actions_left"] = 2  
    game_state["pending_response"] = None
    game_state["pending_beishui"] = None
    game_state["game_started"] = True

    factions = ["司", "丁", "冀"]
    random.shuffle(factions)

    for i, p in enumerate(game_state["players"]):
        p["alive"] = True
        p["hp"] = 5
        p["max_hp"] = 5  
        p["faction"] = factions[i]
        p["revealed"] = False  
        p["death_count"] = 0   
        p["status"] = "正常"       
        p["status_cooldown"] = 0  
        p["yinzhen_turns"] = 0     
        p["status_hand"] = []     
        p["hand"] = []            
        p["skip_next_turn"] = False
        deal_card_to_player(p, 5) 
        
    refresh_status_hands() 
    broadcast_state()

def refresh_status_hands():
    for p in game_state["players"]:
        if p["alive"]:
            p["status_hand"] = random.sample(STATUS_POOL, 2)
    socketio.emit('log', {"msg": "系统：状态牌备选池已更新。"})

def broadcast_state():
    if not game_state["game_started"]:
        lobby_summary = [{"name": p["name"], "idx": p["idx"]} for p in game_state["players"]]
        socketio.emit('lobby_update', {
            "players": lobby_summary,
            "count": len(game_state["players"]),
            "bot_count": game_state["bot_count"]
        })
        return

    for observer in game_state["players"]:
        if observer.get("is_bot"): continue

        players_summary = []
        for p in game_state["players"]:
            visible_faction = p["faction"] if (p["idx"] == observer["idx"] or p["revealed"]) else "隐藏"
            players_summary.append({
                "name": p["name"], "idx": p["idx"], "alive": p["alive"],
                "faction": visible_faction, "hp": p["hp"], "max_hp": p["max_hp"],
                "hand_count": len(p["hand"]), "status": p["status"],
                "status_cooldown": p.get("status_cooldown", 0)
            })
            
        is_my_response = False
        pending_card = ""
        req_defenses = 0
        if game_state["pending_response"] and game_state["pending_response"]["target_idx"] == observer["idx"]:
            is_my_response = True
            pending_card = game_state["pending_response"]["card"]
            req_defenses = game_state["pending_response"].get("required_defenses", 1)

        is_beishui_prompt = (game_state["pending_beishui"] == observer["idx"])

        emit('game_update', {
            "game_started": True,
            "round": game_state["round"],
            "current_idx": game_state["current_idx"],
            "actions_left": game_state["actions_left"], 
            "deck_count": len(game_state["deck"]),
            "players": players_summary,
            "my_cards": observer["hand"],
            "my_status": observer["status"], 
            "my_status_cards": observer.get("status_hand", []),
            "is_my_response": is_my_response,
            "pending_card": pending_card,
            "required_defenses": req_defenses,
            "is_beishui_prompt": is_beishui_prompt
        }, to=observer["sid"])

    check_and_trigger_bot()

def deal_card_to_player(player, count=1):
    batch_drawn = {}
    cards_drawn = 0
    temporary_put_aside = [] 
    while cards_drawn < count:
        if not game_state["deck"]:
            if game_state["discard_pile"]:
                game_state["deck"] = game_state["discard_pile"].copy()
                random.shuffle(game_state["deck"])
                game_state["discard_pile"] = []
                socketio.emit('log', {"msg": "系统：洗牌完毕，弃牌堆已重置回基本牌库。"})
            else:
                break
        card = game_state["deck"].pop()
        current_type_count = batch_drawn.get(card, 0)
        if current_type_count >= 3:
            temporary_put_aside.append(card)
            continue
        else:
            batch_drawn[card] = current_type_count + 1
            player["hand"].append(card)
            cards_drawn += 1
    if temporary_put_aside:
        game_state["deck"].extend(temporary_put_aside)
        random.shuffle(game_state["deck"])

def check_health_and_victory():
    for p in game_state["players"]:
        if p["alive"]:
            if p["hp"] > p["max_hp"]: p["hp"] = p["max_hp"]
            if p["hp"] <= 0:
                if p["faction"] == "丁" and p["death_count"] == 0:
                    p["hp"] = 2
                    p["death_count"] = 1
                    p["revealed"] = True  
                    socketio.emit('log', {"msg": f"系统：{p['name']} 触发【丁】效果，复活并暴露身份！生命值回至 2，摸 2 张牌。"})
                    deal_card_to_player(p, 2)
                else:
                    p["alive"] = False
                    p["hand"] = []
                    p["status_hand"] = []
                    p["status"] = "正常"
                    socketio.emit('log', {"msg": f"系统：{p['name']} 阵亡！身份是【{p['faction']}】。"})

    ji_player = next((p for p in game_state["players"] if p["faction"] == "冀"), None)
    si_player = next((p for p in game_state["players"] if p["faction"] == "司"), None)
    if ji_player and not ji_player["alive"]:
        socketio.emit('log', {"msg": "系统：游戏结束，【司】阵营获得胜利！"})
        game_state["game_started"] = False
        return True
    if si_player and not si_player["alive"]:
        socketio.emit('log', {"msg": "系统：游戏结束，【丁、冀】阵营获得胜利！"})
        game_state["game_started"] = False
        return True
    return False

def execute_play_card(player_idx, card, target_idx, intent):
    current_player = game_state["players"][player_idx]
    if current_player["status"] == "暗度陈仓" and card in ["攻", "防"]: actual_effect = intent
    else: actual_effect = card 

    current_player["hand"].remove(card)
    game_state["discard_pile"].append(card)
    game_state["actions_left"] -= 1  

    if current_player["status"] == "卧薪尝胆" and current_player["hp"] > 1:
        current_player["hp"] -= 1
        socketio.emit('log', {"msg": f"【卧薪尝胆】{current_player['name']}主动出牌，扣减 1 点生命值。"})

    if card != actual_effect:
        socketio.emit('log', {"msg": f"系统：{current_player['name']} 触发【暗度陈仓】，将【{card}】当【{actual_effect}】使用。"})
    
    target_player = game_state["players"][target_idx] if target_idx != -1 else None

    if actual_effect == "回血":
        current_player["hp"] += 1
        socketio.emit('log', {"msg": f"系统：{current_player['name']} 使用了【回血】。"})
    elif actual_effect == "卡牌大师":
        socketio.emit('log', {"msg": f"系统：{current_player['name']} 使用了【卡牌大师】，自动摸 2 张牌。"})
        deal_card_to_player(current_player, 2)
    elif actual_effect == "同归于尽":
        game_state["pending_response"] = {"source_idx": player_idx, "target_idx": target_idx, "card": "同归于尽", "required_defenses": 0, "self_damage": 1}
        socketio.emit('log', {"msg": f"系统：{current_player['name']} 对 {target_player['name']} 丢出【同归于尽】！"})
    elif actual_effect == "荆轲刺秦":
        game_state["pending_response"] = {"source_idx": player_idx, "target_idx": target_idx, "card": "荆轲刺秦", "required_defenses": 2, "self_damage": 1}
        socketio.emit('log', {"msg": f"系统：{current_player['name']} 对 {target_player['name']} 发动【荆轲刺秦】(需2张防)！"})
    elif actual_effect in ["攻", "一字马", "顺手牵羊", "江山易主"]:
        game_state["pending_response"] = {"source_idx": player_idx, "target_idx": target_idx, "card": actual_effect, "required_defenses": 1, "self_damage": 0}
        socketio.emit('log', {"msg": f"系统：{current_player['name']} 对 {target_player['name']} 使用了【{actual_effect}】。"})
        
    check_health_and_victory()
    broadcast_state()

def execute_respond(target_idx, resp_type):
    pending = game_state["pending_response"]
    if not pending: return
    target_player = game_state["players"][target_idx]
    source_player = game_state["players"][pending["source_idx"]]
    card_triggered = pending["card"]

    if resp_type == "攻_as_防":
        target_player["hand"].remove("攻")
        game_state["discard_pile"].append("攻")
        resp_type = "防" 
    elif resp_type == "防":
        target_player["hand"].remove("防")
        game_state["discard_pile"].append("防")

    action_resolved = False  
    cancelled_by_wall = False 

    if resp_type == "防":
        pending["required_defenses"] -= 1
        if pending["required_defenses"] > 0:
            socketio.emit('log', {"msg": f"系统：{target_player['name']} 挡住了一闪，但仍需再打出 1 张【防】！"})
            broadcast_state()
            return
        else:
            socketio.emit('log', {"msg": f"系统：{target_player['name']} 闪避成功，完美招架。"})
            action_resolved = True
            game_state["pending_response"] = None
            
    elif resp_type == "长城":
        target_player["hand"].remove("长城")
        game_state["discard_pile"].append("长城")
        socketio.emit('log', {"msg": f"系统：{target_player['name']} 筑起【长城】，直接无效了对自身的【{card_triggered}】！"})
        action_resolved = True
        cancelled_by_wall = True 
        game_state["pending_response"] = None
        
    elif resp_type == "放弃":
        action_resolved = True
        if card_triggered == "攻":
            dmg = 1
            if target_player["status"] == "卧薪尝胆": dmg -= 1
            target_player["hp"] -= dmg
            socketio.emit('log', {"msg": f"系统：{target_player['name']} 放弃抵抗，受到 1 点伤害。"})
        elif card_triggered == "荆轲刺秦":
            dmg = 2
            if target_player["status"] == "卧薪尝胆": dmg -= 1
            target_player["hp"] -= dmg
            socketio.emit('log', {"msg": f"系统：暴击！{target_player['name']} 承受了 {dmg} 点刺杀伤害！"})
        elif card_triggered == "同归于尽":
            target_player["hp"] -= 1
            socketio.emit('log', {"msg": f"系统：同归于尽生效，{target_player['name']} 失去 1 点生命值。"})
        elif card_triggered == "一字马":
            target_player["skip_next_turn"] = True
            socketio.emit('log', {"msg": f"系统：{target_player['name']} 战马受惊，下回合将被强行跳过！"})
        elif card_triggered == "顺手牵羊":
            if target_player["hand"]:
                stolen = random.choice(target_player["hand"])
                target_player["hand"].remove(stolen)
                source_player["hand"].append(stolen)
                socketio.emit('log', {"msg": f"系统：{source_player['name']} 从 {target_player['name']} 怀里摸走了一张底牌！"})
        elif card_triggered == "江山易主":
            target_player["hand"], source_player["hand"] = source_player["hand"], target_player["hand"]
            socketio.emit('log', {"msg": f"系统：{source_player['name']} 与 {target_player['name']} 瞬间全身手牌大互换！"})
        game_state["pending_response"] = None

    if action_resolved and pending and pending.get("self_damage", 0) > 0:
        if cancelled_by_wall:
            socketio.emit('log', {"msg": f"系统：由于卡牌被【长城】化解，使用者 {source_player['name']} 规避了反噬自伤。"})
        else:
            sd = pending["self_damage"]
            source_player["hp"] -= sd
            socketio.emit('log', {"msg": f"系统：使用方 {source_player['name']} 受到 {sd} 点反噬扣血。"})

    check_health_and_victory()
    broadcast_state()

def execute_equip_status(player_idx, card):
    current_player = game_state["players"][player_idx]
    current_player["status_hand"].remove(card)
    current_player["status"] = card
    current_player["status_cooldown"] = 3 
    current_player["yinzhen_turns"] = 0   
    if card == "背水一战": current_player["max_hp"] = 5
    elif card == "饮鸩止渴": current_player["max_hp"] = 10; current_player["hp"] = 10 
    elif card == "卧薪尝胆": current_player["max_hp"] = 5
    socketio.emit('log', {"msg": f"系统：{current_player['name']} 装备了状态：【{card}】！"})
    check_health_and_victory()
    broadcast_state()

def execute_end_turn():
    current_player = game_state["players"][game_state["current_idx"]]
    for p in game_state["players"]:
        if p["alive"] and p.get("status_cooldown", 0) > 0:
            p["status_cooldown"] -= 1

    if current_player["alive"] and current_player["status"] == "饮鸩止渴":
        current_player["yinzhen_turns"] += 1
        if current_player["yinzhen_turns"] % 3 == 0:
            current_player["max_hp"] = max(1, current_player["max_hp"] - 2)
            current_player["hp"] = min(current_player["max_hp"], current_player["hp"] + 2)
            socketio.emit('log', {"msg": f"【饮鸩止渴】毒性发作：{current_player['name']} 最大体力扣减 2，当前体力回 2。"})
            
    check_health_and_victory()

    while True:
        nxt_idx = (game_state["current_idx"] + 1) % len(game_state["players"])
        game_state["current_idx"] = nxt_idx
        next_player = game_state["players"][nxt_idx]
        
        if nxt_idx == 0:
            for p in game_state["players"]:
                if p["alive"] and len(p["hand"]) < 5:
                    deal_card_to_player(p, 5 - len(p["hand"]))
            game_state["round"] += 1
            if game_state["round"] % 2 == 1:
                refresh_status_hands()

        if not next_player["alive"]: continue
        if next_player.get("skip_next_turn", False):
            next_player["skip_next_turn"] = False
            continue 
        break

    game_state["actions_left"] = min(5, game_state["round"] + 1)
    if next_player["status"] == "背水一战":
        game_state["pending_beishui"] = next_player["idx"]
        socketio.emit('log', {"msg": f"系统：等待 {next_player['name']} 抉择【背水一战】。"})

    broadcast_state()

def check_and_trigger_bot():
    if not game_state["game_started"]: return
    is_bot_needed = False
    if game_state["pending_response"] and game_state["players"][game_state["pending_response"]["target_idx"]].get("is_bot"):
        is_bot_needed = True
    elif game_state["pending_beishui"] is not None and game_state["players"][game_state["pending_beishui"]].get("is_bot"):
        is_bot_needed = True
    elif game_state["players"][game_state["current_idx"]].get("is_bot") and not game_state["pending_response"] and game_state["pending_beishui"] is None:
        is_bot_needed = True

    if is_bot_needed and not game_state["bot_running"]:
        game_state["bot_running"] = True
        socketio.start_background_task(bot_brain_worker)

# ==================== 🤖 人机自动化脑电波控制核心（情报集火流升级版） ====================

def bot_brain_worker():
    socketio.sleep(1.0) 
    game_state["bot_running"] = False 
    
    if not game_state["game_started"]: return

    # 情况 A：被动自卫防御
    pending = game_state["pending_response"]
    if pending:
        t_idx = pending["target_idx"]
        bot = game_state["players"][t_idx]
        if bot.get("is_bot"):
            card_triggered = pending["card"]
            has_def = "防" in bot["hand"]
            has_atk_as_def = (bot["status"] == "暗度陈仓" and "攻" in bot["hand"])
            has_wall = "长城" in bot["hand"]

            resp = "放弃"
            if card_triggered in ["攻", "荆轲刺秦"]:
                if has_def: resp = "防"
                elif has_atk_as_def: resp = "攻_as_防"
                elif has_wall: resp = "长城"
            else:
                if has_wall: resp = "长城"
            
            execute_respond(t_idx, resp)
            return

    # 情况 B：背水一战抉择
    if game_state["pending_beishui"] is not None:
        b_idx = game_state["pending_beishui"]
        bot = game_state["players"][b_idx]
        if bot.get("is_bot"):
            game_state["pending_beishui"] = None
            socketio.emit('log', {"msg": f"系统：{bot['name']} (人机) 在【背水一战】中稳妥地选择不扣减生命值。"})
            check_health_and_victory()
            broadcast_state()
            return

    # 情况 C：主动出牌阶段
    c_idx = game_state["current_idx"]
    bot = game_state["players"][c_idx]
    if bot.get("is_bot"):
        if bot["status"] == "正常" and bot["status_cooldown"] == 0 and bot["status_hand"]:
            execute_equip_status(c_idx, random.choice(bot["status_hand"]))
            return

        if game_state["actions_left"] > 0:
            # 1. 初始获取所有活着的对手
            opponents = [p["idx"] for p in game_state["players"] if p["alive"] and p["idx"] != c_idx]
            
            # 🔍 【核心注入】：判断是否触发天眼集火逻辑
            target_idx = -1
            if bot["faction"] in ["冀", "司"]:
                # 寻找场上是否已经有【存活且亮明身份】的丁玩家
                revealed_ding = next((p for p in game_state["players"] if p["alive"] and p["faction"] == "丁" and p.get("revealed")), None)
                
                if revealed_ding:
                    # 触发排除法：过滤掉丁玩家，必须锁定那个【不是丁】的玩家
                    valid_targets = [idx for idx in opponents if idx != revealed_ding["idx"]]
                    if valid_targets:
                        target_idx = valid_targets[0] # 因为一共3个人，排除自己和丁，必然只剩唯一的精准目标
                        
            # 2. 如果人机自己是丁，或者场上没有亮明的丁（啥都不知道），则退回到随机盲打
            if target_idx == -1 and opponents:
                target_idx = random.choice(opponents)

            for card in bot["hand"]:
                if card == "回血" and bot["hp"] < bot["max_hp"]:
                    execute_play_card(c_idx, "回血", -1, "回血")
                    return
                if card == "卡牌大师":
                    execute_play_card(c_idx, "卡牌大师", -1, "卡牌大师")
                    return
                if card in ["攻", "荆轲刺秦", "一字马", "顺手牵羊", "同归于尽", "江山易主"] and target_idx != -1:
                    execute_play_card(c_idx, card, target_idx, card)
                    return
                if card == "防" and bot["status"] == "暗度陈仓" and target_idx != -1:
                    execute_play_card(c_idx, "防", target_idx, "攻")
                    return

        execute_end_turn()

# ==================== 🌐 网络通讯区 ====================

def start_game_with_bots():
    needed_bots = 3 - len(game_state["players"])
    for i in range(needed_bots):
        idx = len(game_state["players"])
        game_state["players"].append({
            "sid": f"bot_virtual_{idx}", "name": f"人机大师-{idx+1}号", "idx": idx, "alive": True,
            "hp": 5, "max_hp": 5, "hand": [], "status": "正常", "faction": "", "revealed": False, "death_count": 0, "skip_next_turn": False,
            "status_hand": [], "status_cooldown": 0, "yinzhen_turns": 0, "is_bot": True 
        })
    socketio.emit('log', {"msg": f"系统：全员集结（成功注入 {game_state['bot_count']} 个人机单位）！游戏开始！"})
    init_game()

@app.route('/')
def index(): return render_template('index.html')

@socketio.on('join_game')
def handle_join(data=None):
    sid = request.sid
    for p in game_state["players"]:
        if p["sid"] == sid: return
    if game_state["game_started"] or len(game_state["players"]) >= 3: 
        emit('action_error', {"msg": "人满或已开局！"})
        return
        
    idx = len(game_state["players"])
    p_name = f"英雄 {idx + 1}"
    if data and data.get("name"):
        custom_name = data.get("name").strip()
        if custom_name: p_name = custom_name

    game_state["players"].append({
        "sid": sid, "name": p_name, "idx": idx, "alive": True,
        "hp": 5, "max_hp": 5, "hand": [], "status": "正常", "faction": "", "revealed": False, "death_count": 0, "skip_next_turn": False,
        "status_hand": [], "status_cooldown": 0, "yinzhen_turns": 0
    })
    socketio.emit('log', {"msg": f"系统：【{p_name}】已经入场。"})
    
    if len(game_state["players"]) + game_state["bot_count"] >= 3:
        start_game_with_bots()
    else:
        broadcast_state()

@socketio.on('change_bot_count')
def handle_change_bot_count(data):
    if game_state["game_started"]: return
    try:
        bc = int(data.get("bot_count", 0))
        if bc in [0, 1, 2]:
            game_state["bot_count"] = bc
            if len(game_state["players"]) + game_state["bot_count"] >= 3:
                start_game_with_bots()
            else:
                broadcast_state()
    except: pass

@socketio.on('equip_status')
def handle_equip_status(data):
    current_player = game_state["players"][game_state["current_idx"]]
    if current_player["sid"] != request.sid or game_state["pending_response"] or game_state["pending_beishui"] is not None: return
    card = data.get("card")
    if card not in current_player["status_hand"]: return
    if current_player["status_cooldown"] > 0:
        emit('action_error', {"msg": "状态冷却中。"})
        return
    execute_equip_status(game_state["current_idx"], card)

@socketio.on('beishui_decision')
def handle_beishui_decision(data):
    idx = game_state["pending_beishui"]
    if idx is None: return
    player = game_state["players"][idx]
    if player["sid"] != request.sid: return
    try: x = int(data.get("sacrifice", 0))
    except: x = 0
    if x < 0 or x >= player["hp"]: x = 0
    
    if x > 0:
        player["hp"] -= x
        socketio.emit('log', {"msg": f"系统：{player['name']} 触发【背水一战】自残 {x} 点并摸牌。"})
        deal_card_to_player(player, x + 1)
    game_state["pending_beishui"] = None 
    check_health_and_victory()
    broadcast_state()

@socketio.on('play_card')
def handle_play_card(data):
    current_player = game_state["players"][game_state["current_idx"]]
    if current_player["sid"] != request.sid or game_state["pending_response"] or game_state["pending_beishui"] is not None: return
    card = data.get("card")
    target_idx = int(data.get("target", -1))
    intent = data.get("intent", card) 
    if card not in current_player["hand"]: return
    
    if current_player["status"] == "暗度陈仓" and card in ["攻", "防"]: actual_effect = intent
    else: actual_effect = card 

    if actual_effect in ["防", "长城"]:
        emit('action_error', {"msg": "被动响应牌，不可直接打出。"})
        return
    if game_state["actions_left"] <= 0:
        emit('action_error', {"msg": "行动力耗尽。"})
        return
    if actual_effect in ["攻", "荆轲刺秦", "一字马", "顺手牵羊", "江山易主", "同归于尽"]:
        if target_idx == -1 or target_idx == game_state["current_idx"]:
            emit('action_error', {"msg": "请先在上方点击选定目标玩家！"})
            return
    if actual_effect == "回血" and current_player["hp"] >= current_player["max_hp"]:
        emit('action_error', {"msg": "血量已满。"})
        return

    execute_play_card(game_state["current_idx"], card, target_idx, intent)

@socketio.on('respond_action')
def handle_respond(data):
    resp_type = data.get("type") 
    pending = game_state["pending_response"]
    if not pending: return
    target_player = game_state["players"][pending["target_idx"]]
    if target_player["sid"] != request.sid: return
    execute_respond(pending["target_idx"], resp_type)

@socketio.on('end_turn')
def handle_end_turn():
    if game_state["players"][game_state["current_idx"]]["sid"] != request.sid or game_state["pending_response"] or game_state["pending_beishui"] is not None: return
    execute_end_turn()

@socketio.on('reset_game')
def handle_reset():
    global game_state
    game_state = {
        "players": [], "deck": [], "discard_pile": [], "round": 1, "current_idx": 0,
        "actions_left": 2, "game_started": False, "pending_response": None, "pending_beishui": None,
        "bot_count": 0, "bot_running": False
    }
    socketio.emit('force_reload_all')

if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)
