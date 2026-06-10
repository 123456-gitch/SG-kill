import random
import time
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit

print("[系统重构] 三国kill·底层状态流深层修复版正在全速注入...")

app = Flask(__name__)
app.config['SECRET_KEY'] = 'three_kingdoms_kill_v7_final'
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
    socketio.emit('log', {"msg": "系统：本轮全场状态卡牌备选池已洗牌重置。"})

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
            "my_idx": observer["idx"], 
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
                socketio.emit('log', {"msg": "系统：牌堆枯竭！已自动回收弃牌堆洗牌重组。"})
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
                    socketio.emit('log', {"msg": f"✨ 核心护卫：{p['name']} 触发【丁】免死效果，绝地复活！当前血量变为 2，真实身份公开！"})
                    deal_card_to_player(p, 2)
                else:
                    p["alive"] = False
                    p["hand"] = []
                    p["status_hand"] = []
                    p["status"] = "正常"
                    socketio.emit('log', {"msg": f"💀 阵亡通告：{p['name']} 饮恨沙场！其最终隐藏身份是【{p['faction']}】。"})

    ji_player = next((p for p in game_state["players"] if p["faction"] == "冀"), None)
    si_player = next((p for p in game_state["players"] if p["faction"] == "司"), None)
    if ji_player and not ji_player["alive"]:
        socketio.emit('log', {"msg": "🏆 战局终结：【司】阵营成功斩首【冀】，获得独立胜利！"})
        game_state["game_started"] = False
        return True
    if si_player and not si_player["alive"]:
        socketio.emit('log', {"msg": "🏆 战局终结：叛逆者【司】已被剿灭，【丁、冀】同盟阵营共享胜利！"})
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
        socketio.emit('log', {"msg": f"⚔️【卧薪尝胆】{current_player['name']} 忍痛出牌，受到 1 点执念自残伤害。"})

    if card != actual_effect:
        socketio.emit('log', {"msg": f"🎭 诡道：{current_player['name']} 虚晃一枪【暗度陈仓】，将【{card}】伪装成【{actual_effect}】打出！"})
    
    target_player = game_state["players"][target_idx] if target_idx != -1 else None

    if actual_effect == "回血":
        current_player["hp"] += 1
        socketio.emit('log', {"msg": f"🧪 补给：{current_player['name']} 饮用了【回血】药剂。"})
    elif actual_effect == "卡牌大师":
        socketio.emit('log', {"msg": f"🔮 秘术：{current_player['name']} 启动【卡牌大师】，瞬间补充 2 张手牌。"})
        deal_card_to_player(current_player, 2)
    elif actual_effect == "同归于尽":
        game_state["pending_response"] = {"source_idx": player_idx, "target_idx": target_idx, "card": "同归于尽", "required_defenses": 0, "self_damage": 1}
        socketio.emit('log', {"msg": f"🔥 狂暴：{current_player['name']} 对 {target_player['name']} 发动【同归于尽】！"})
    elif actual_effect == "荆轲刺秦":
        game_state["pending_response"] = {"source_idx": player_idx, "target_idx": target_idx, "card": "荆轲刺秦", "required_defenses": 2, "self_damage": 1}
        socketio.emit('log', {"msg": f"🗡️ 绝杀：{current_player['name']} 对 {target_player['name']} 发动【荆轲刺秦】(目标需连续打出 2 张防)！"})
    elif actual_effect in ["攻", "一字马", "顺手牵羊", "江山易主"]:
        game_state["pending_response"] = {"source_idx": player_idx, "target_idx": target_idx, "card": actual_effect, "required_defenses": 1, "self_damage": 0}
        socketio.emit('log', {"msg": f"🎯 锋芒：{current_player['name']} 锁定了 {target_player['name']}，扔出了一张【{actual_effect}】。"})
        
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
            socketio.emit('log', {"msg": f"🛡️ 招架：{target_player['name']} 挡下一击，但【荆轲刺秦】攻势未止，仍需再打出一张【防】！"})
            broadcast_state()
            return
        else:
            socketio.emit('log', {"msg": f"✨ 闪避：{target_player['name']} 步伐轻盈，化解了本次攻势。"})
            action_resolved = True
            game_state["pending_response"] = None
            
    elif resp_type == "长城":
        target_player["hand"].remove("长城")
        game_state["discard_pile"].append("长城")
        socketio.emit('log', {"msg": f"🧱 雄关：{target_player['name']} 祭出【长城】防御壁，让对方的【{card_triggered}】烟消瓦解！"})
        action_resolved = True
        cancelled_by_wall = True 
        game_state["pending_response"] = None
        
    elif resp_type == "放弃":
        action_resolved = True
        if card_triggered == "攻":
            dmg = 1
            if target_player["status"] == "卧薪尝胆": dmg -= 1
            target_player["hp"] -= dmg
            socketio.emit('log', {"msg": f"💥 💥 命中：{target_player['name']} 放弃挣扎，承受 {dmg} 点伤害。"})
        elif card_triggered == "荆轲刺秦":
            dmg = 2
            if target_player["status"] == "卧薪尝胆": dmg -= 1
            target_player["hp"] -= dmg
            socketio.emit('log', {"msg": f"🩸 暴击：刺客得手！{target_player['name']} 痛失 {dmg} 点生命值！"})
            # 💥【核心修复】：遵循严密结算逻辑，先对别人造成伤害，对方结算完后，出牌者延迟扣血1
            source_player["hp"] -= 1
            socketio.emit('log', {"msg": f"☣️ 刺秦反噬：暗杀爆发结束！出牌者 {source_player['name']} 受到反噬伤害扣减 1 点血量。"})
            pending["self_damage"] = 0 # 已经扣过，清除标志位
        elif card_triggered == "同归于尽":
            target_player["hp"] -= 1
            socketio.emit('log', {"msg": f"💥 碎裂：爆炸波及了 {target_player['name']}，无视防备扣血 1 点。"})
        elif card_triggered == "一字马":
            target_player["skip_next_turn"] = True
            socketio.emit('log', {"msg": f"⛓️ 禁锢：{target_player['name']} 被绊住，下个回合将被强制跳过！"})
        elif card_triggered == "顺手牵羊":
            if target_player["hand"]:
                stolen = random.choice(target_player["hand"])
                target_player["hand"].remove(stolen)
                source_player["hand"].append(stolen)
                socketio.emit('log', {"msg": f"🦅 掠夺：{source_player['name']} 伸手从 {target_player['name']} 的怀里偷走了一张手牌！"})
        elif card_triggered == "江山易主":
            target_player["hand"], source_player["hand"] = source_player["hand"], target_player["hand"]
            socketio.emit('log', {"msg": f"🔄 颠倒乾坤：{source_player['name']} 与 {target_player['name']} 进行了全身手牌大对调！"})
        game_state["pending_response"] = None

    # 处理其余卡牌的通用自损（如同归于尽）
    if action_resolved and pending and pending.get("self_damage", 0) > 0:
        if not cancelled_by_wall:
            sd = pending["self_damage"]
            source_player["hp"] -= sd
            socketio.emit('log', {"msg": f"☣️ 反噬：{source_player['name']} 自身也扣减了 {sd} 点血量。"})

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
    socketio.emit('log', {"msg": f"⚡ 觉醒：{current_player['name']} 开启了暴走姿态 ——【{card}】！"})
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
            socketio.emit('log', {"msg": f"☣️【饮鸩止渴】慢性爆发！{current_player['name']} 上限永久扣减 2，血量强制调整。"})
            
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
            socketio.emit('log', {"msg": f"⛓️ 系统：{next_player['name']} 受到一字马影响，强行跳过本轮！"})
            continue 
        break

    game_state["actions_left"] = min(5, game_state["round"] + 1)
    if next_player["status"] == "背水一战":
        game_state["pending_beishui"] = next_player["idx"]
        socketio.emit('log', {"msg": f"🎲 抉择时分：等待 {next_player['name']} 选择献祭层数。"})

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

def bot_brain_worker():
    socketio.sleep(1.0) 
    game_state["bot_running"] = False 
    
    if not game_state["game_started"]: return

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

    if game_state["pending_beishui"] is not None:
        b_idx = game_state["pending_beishui"]
        bot = game_state["players"][b_idx]
        if bot.get("is_bot"):
            game_state["pending_beishui"] = None
            socketio.emit('log', {"msg": f"🤖 {bot['name']} (人机) 开启回合，背水一战稳健放弃自残。"})
            check_health_and_victory()
            broadcast_state()
            return

    c_idx = game_state["current_idx"]
    bot = game_state["players"][c_idx]
    if bot.get("is_bot"):
        if bot["status"] == "正常" and bot.get("status_cooldown", 0) == 0 and bot.get("status_hand"):
            chosen_status = random.choice(bot["status_hand"])
            execute_equip_status(c_idx, chosen_status)
            return 

        if game_state["actions_left"] > 0:
            opponents = [p["idx"] for p in game_state["players"] if p["alive"] and p["idx"] != c_idx]
            target_idx = -1
            if bot["faction"] in ["冀", "司"]:
                revealed_ding = next((p for p in game_state["players"] if p["alive"] and p["faction"] == "丁" and p.get("revealed")), None)
                if revealed_ding:
                    valid_targets = [idx for idx in opponents if idx != revealed_ding["idx"]]
                    if valid_targets: target_idx = valid_targets[0]
                        
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

        socketio.emit('log', {"msg": f"🤖 {bot['name']} (人机) 评估完毕：检测到无牌可出或缺乏行动力，自主结束出牌。"})
        execute_end_turn()

def start_game_with_bots():
    needed_bots = 3 - len(game_state["players"])
    for i in range(needed_bots):
        idx = len(game_state["players"])
        game_state["players"].append({
            "sid": f"bot_virtual_{idx}", "name": f"人机大师-{idx+1}号", "idx": idx, "alive": True,
            "hp": 5, "max_hp": 5, "hand": [], "status": "正常", "faction": "", "revealed": False, "death_count": 0, "skip_next_turn": False,
            "status_hand": [], "status_cooldown": 0, "yinzhen_turns": 0, "is_bot": True 
        })
    socketio.emit('log', {"msg": f"🚀 战火重燃：满 3 人，已成功补充 {game_state['bot_count']} 名AI选手，直接开局！"})
    init_game()

@app.route('/')
def index(): return render_template('index.html')

@socketio.on('join_game')
def handle_join(data=None):
    sid = request.sid
    for p in game_state["players"]:
        if p["sid"] == sid: return
    if game_state["game_started"] or len(game_state["players"]) >= 3: 
        emit('action_error', {"msg": "房间已满员或对局已经开始了哦！"})
        return
        
    idx = len(game_state["players"])
    p_name = f"战神 {idx + 1}"
    if data and data.get("name"):
        custom_name = data.get("name").strip()
        if custom_name: p_name = custom_name

    game_state["players"].append({
        "sid": sid, "name": p_name, "idx": idx, "alive": True,
        "hp": 5, "max_hp": 5, "hand": [], "status": "正常", "faction": "", "revealed": False, "death_count": 0, "skip_next_turn": False,
        "status_hand": [], "status_cooldown": 0, "yinzhen_turns": 0
    })
    socketio.emit('log', {"msg": f"👋 玩家 【{p_name}】 迈入竞技大厅。"})
    
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
    if current_player["sid"] != request.sid:
        emit('action_error', {"msg": "❌ 操作失败：现在不是你的回合，无法使用装备牌！"})
        return
    if game_state["pending_response"] or game_state["pending_beishui"] is not None:
        emit('action_error', {"msg": "❌ 操作失败：当前有挂起的博弈结算，请先处理完再用装备！"})
        return
    card = data.get("card")
    if card not in current_player["status_hand"]: return
    if current_player["status_cooldown"] > 0:
        emit('action_error', {"msg": "状态牌处于冷却中，无法强行觉醒！"})
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
        socketio.emit('log', {"msg": f"🩸 孤注一掷：{player['name']} 激活【背水一战】自残 {x} 点并狂摸 {x+1} 张牌！"})
        deal_card_to_player(player, x + 1)
    game_state["pending_beishui"] = None 
    check_health_and_victory()
    broadcast_state()

@socketio.on('play_card')
def handle_play_card(data):
    current_player = game_state["players"][game_state["current_idx"]]
    if current_player["sid"] != request.sid:
        emit('action_error', {"msg": "❌ 越权拦截：现在是别人的出牌时间，请等一等哦！"})
        return
    if game_state["pending_response"] or game_state["pending_beishui"] is not None:
        emit('action_error', {"msg": "❌ 越权拦截：战场当前有弹窗响应正在挂起，请先完成处理。"})
        return

    card = data.get("card")
    target_idx = int(data.get("target", -1))
    intent = data.get("intent", card) 
    if card not in current_player["hand"]: return
    
    if current_player["status"] == "暗度陈仓" and card in ["攻", "防"]: actual_effect = intent
    else: actual_effect = card 

    if actual_effect in ["防", "长城"]:
        emit('action_error', {"msg": "此牌属于被动响应牌，请等敌人打你时，在中央弹窗里点击使用！"})
        return
    if game_state["actions_left"] <= 0:
        emit('action_error', {"msg": "本回合行动力耗尽啦！请点击右侧绿色按钮结束本阶段。"})
        return
    if actual_effect in ["攻", "荆轲刺秦", "一字马", "顺手牵羊", "同归于尽", "江山易主"]:
        if target_idx == -1 or target_idx == game_state["current_idx"]:
            emit('action_error', {"msg": "🎯 目标锁定失败：此卡属于定向打击牌，请先在上方点击选取其他玩家头像框（亮红圈高亮）再出牌！"})
            return
    if actual_effect == "回血" and current_player["hp"] >= current_player["max_hp"]:
        emit('action_error', {"msg": "血量已满，吃药无效！"})
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
    if game_state["players"][game_state["current_idx"]]["sid"] != request.sid or game_state["pending_response"] or game_state["pending_beishui"] is not None: 
        emit('action_error', {"msg": "❌ 越权拦截：现在不能交出回合。"})
        return
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
