import random
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit

print("[系统通知] 三国kill 服务端核心已启动...")

app = Flask(__name__)
app.config['SECRET_KEY'] = 'three_kingdoms_kill_v4_3'
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
    "pending_beishui": None  
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
    socketio.emit('log', {"msg": "系统：状态牌备选池已更新（状态牌未装备前不生效）。"})

def broadcast_state():
    if not game_state["game_started"]: return
    for observer in game_state["players"]:
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
            if p["hp"] > p["max_hp"]:
                p["hp"] = p["max_hp"]
                socketio.emit('log', {"msg": f"系统：{p['name']} 的生命值超过上限，已被调整为 {p['max_hp']}。"})

            if p["hp"] <= 0:
                if p["faction"] == "丁" and p["death_count"] == 0:
                    p["hp"] = 2
                    p["death_count"] = 1
                    p["revealed"] = True  
                    socketio.emit('log', {"msg": f"系统：{p['name']} 触发【丁】阵营效果，生命值恢复至 2，摸 2 张牌。"})
                    deal_card_to_player(p, 2)
                    if p["hp"] > p["max_hp"]: p["hp"] = p["max_hp"]
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

@app.route('/')
def index(): return render_template('index.html')

@socketio.on('join_game')
def handle_join():
    sid = request.sid
    for p in game_state["players"]:
        if p["sid"] == sid: return
    if game_state["game_started"] or len(game_state["players"]) >= 3: return
    idx = len(game_state["players"])
    p_name = f"玩家 {idx + 1}"
    game_state["players"].append({
        "sid": sid, "name": p_name, "idx": idx, "alive": True,
        "hp": 5, "max_hp": 5, "hand": [], "status": "正常", "faction": "", "revealed": False, "death_count": 0, "skip_next_turn": False,
        "status_hand": [], "status_cooldown": 0, "yinzhen_turns": 0
    })
    socketio.emit('log', {"msg": f"系统：{p_name} 进入游戏。"})
    if len(game_state["players"]) == 3:
        init_game()
    broadcast_state()

@socketio.on('equip_status')
def handle_equip_status(data):
    current_player = game_state["players"][game_state["current_idx"]]
    if current_player["sid"] != request.sid or game_state["pending_response"] or game_state["pending_beishui"] is not None:
        return
    
    card = data.get("card")
    if card not in current_player["status_hand"]: return
    
    if current_player["status_cooldown"] > 0:
        emit('action_error', {"msg": f"状态牌处于冷却中，还需要等待 {current_player['status_cooldown']} 回合。"})
        return
        
    current_player["status_hand"].remove(card)
    current_player["status"] = card
    current_player["status_cooldown"] = 3 
    current_player["yinzhen_turns"] = 0   
    
    if card == "背水一战":
        current_player["max_hp"] = 5
    elif card == "饮鸩止渴":
        current_player["max_hp"] = 10
        current_player["hp"] = 10 
    elif card == "卧薪尝胆":
        current_player["max_hp"] = 5
        
    socketio.emit('log', {"msg": f"系统：{current_player['name']} 装备了状态牌【{card}】。"})
    check_health_and_victory()
    broadcast_state()

@socketio.on('beishui_decision')
def handle_beishui_decision(data):
    idx = game_state["pending_beishui"]
    if idx is None: return
    player = game_state["players"][idx]
    if player["sid"] != request.sid: return
    try:
        x = int(data.get("sacrifice", 0))
    except:
        x = 0
    if x < 0 or x >= player["hp"]: x = 0
    
    if x > 0:
        player["hp"] -= x
        socketio.emit('log', {"msg": f"系统：{player['name']} 触发【背水一战】，失去 {x} 点生命值，摸了 {x+1} 张卡牌。"})
        deal_card_to_player(player, x + 1)
    else:
        socketio.emit('log', {"msg": f"系统：{player['name']} 未选择失去生命值。"})
        
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
    
    if current_player["status"] == "暗度陈仓" and card in ["攻", "防"]:
        actual_effect = intent
    else:
        actual_effect = card 

    if actual_effect in ["防", "长城"]:
        emit('action_error', {"msg": f"【{actual_effect}】属于被动响应牌，无法主动使用。"})
        return
        
    if game_state["actions_left"] <= 0:
        emit('action_error', {"msg": "本回合出牌次数已耗尽。"})
        return
        
    if actual_effect in ["攻", "荆轲刺秦", "一字马", "顺手牵羊", "江山易主", "同归于尽"]:
        if target_idx == -1 or target_idx == game_state["current_idx"]:
            emit('action_error', {"msg": "请先在上方点击选定目标玩家。"})
            return
        if not game_state["players"][target_idx]["alive"]: return
        
    if actual_effect == "回血" and current_player["hp"] >= current_player["max_hp"]:
        emit('action_error', {"msg": "你当前生命值已满，无法使用【回血】。"})
        return

    current_player["hand"].remove(card)
    game_state["discard_pile"].append(card)
    game_state["actions_left"] -= 1  

    if current_player["status"] == "卧薪尝胆":
        if current_player["hp"] > 1:
            current_player["hp"] -= 1
            socketio.emit('log', {"msg": f"【卧薪尝胆】效果：{current_player['name']} 使用卡牌，自身失去 1 点生命值。"})

    if card != actual_effect:
        socketio.emit('log', {"msg": f"系统：{current_player['name']} 触发【暗度陈仓】，将【{card}】作为【{actual_effect}】使用。"})
    
    target_player = game_state["players"][target_idx] if target_idx != -1 else None

    if actual_effect == "回血":
        current_player["hp"] += 1
        socketio.emit('log', {"msg": f"系统：{current_player['name']} 使用了【回血】。"})
    elif actual_effect == "卡牌大师":
        socketio.emit('log', {"msg": f"系统：{current_player['name']} 使用了【卡牌大师】，摸 2 张牌。"})
        deal_card_to_player(current_player, 2)
    elif actual_effect == "同归于尽":
        game_state["pending_response"] = {
            "source_idx": game_state["current_idx"], "target_idx": target_idx, "card": "同归于尽", "required_defenses": 0, "self_damage": 1
        }
        socketio.emit('log', {"msg": f"系统：{current_player['name']} 对 {target_player['name']} 使用了【同归于尽】，等待对方响应。"})
    elif actual_effect == "荆轲刺秦":
        game_state["pending_response"] = {
            "source_idx": game_state["current_idx"], "target_idx": target_idx, "card": "荆轲刺秦", "required_defenses": 2, "self_damage": 1
        }
        socketio.emit('log', {"msg": f"系统：{current_player['name']} 对 {target_player['name']} 使用了【荆轲刺秦】（需要 2 张防）。"})
    elif actual_effect in ["攻", "一字马", "顺手牵羊", "江山易主"]:
        game_state["pending_response"] = {
            "source_idx": game_state["current_idx"], "target_idx": target_idx, "card": actual_effect, "required_defenses": 1, "self_damage": 0
        }
        socketio.emit('log', {"msg": f"系统：{current_player['name']} 对 {target_player['name']} 使用了【{actual_effect}】。"})
        
    check_health_and_victory()
    broadcast_state()

@socketio.on('respond_action')
def handle_respond(data):
    resp_type = data.get("type") 
    pending = game_state["pending_response"]
    if not pending: return
    target_player = game_state["players"][pending["target_idx"]]
    source_player = game_state["players"][pending["source_idx"]]
    if target_player["sid"] != request.sid: return
    card_triggered = pending["card"]

    if resp_type == "攻_as_防":
        if target_player["status"] != "暗度陈仓" or "攻" not in target_player["hand"]: return
        target_player["hand"].remove("攻")
        game_state["discard_pile"].append("攻")
        resp_type = "防" 
    elif resp_type == "防":
        if "防" not in target_player["hand"]: return
        target_player["hand"].remove("防")
        game_state["discard_pile"].append("防")

    action_resolved = False  
    cancelled_by_wall = False 

    if resp_type == "防":
        pending["required_defenses"] -= 1
        if pending["required_defenses"] > 0:
            socketio.emit('log', {"msg": f"系统：{target_player['name']} 使用了【防】，还需要再打出 1 张【防】。"})
            broadcast_state()
            return
        else:
            socketio.emit('log', {"msg": f"系统：{target_player['name']} 使用【防】成功抵挡了攻击。"})
            action_resolved = True
            game_state["pending_response"] = None
            
    elif resp_type == "长城":
        if "长城" not in target_player["hand"]: return
        target_player["hand"].remove("长城")
        game_state["discard_pile"].append("长城")
        socketio.emit('log', {"msg": f"系统：{target_player['name']} 使用【长城】，使 {source_player['name']} 的【{card_triggered}】无效。"})
        action_resolved = True
        cancelled_by_wall = True 
        game_state["pending_response"] = None
        
    elif resp_type == "放弃":
        action_resolved = True
        if card_triggered == "攻":
            dmg = 1
            if target_player["status"] == "卧薪尝胆": dmg -= 1
            target_player["hp"] -= dmg
            socketio.emit('log', {"msg": f"系统：{target_player['name']} 受到 1 点伤害。"})
        elif card_triggered == "荆轲刺秦":
            dmg = 2
            if target_player["status"] == "卧薪尝胆": dmg -= 1
            target_player["hp"] -= dmg
            socketio.emit('log', {"msg": f"系统：{target_player['name']} 受到 {dmg} 点伤害。"})
        elif card_triggered == "同归于尽":
            target_player["hp"] -= 1
            socketio.emit('log', {"msg": f"系统：【同归于尽】生效，{target_player['name']} 失去 1 点生命值。"})
        elif card_triggered == "一字马":
            target_player["skip_next_turn"] = True
            socketio.emit('log', {"msg": f"系统：{target_player['name']} 受到【一字马】效果影响，跳过其下一个回合。"})
        elif card_triggered == "顺手牵羊":
            if target_player["hand"]:
                stolen = random.choice(target_player["hand"])
                target_player["hand"].remove(stolen)
                source_player["hand"].append(stolen)
                socketio.emit('log', {"msg": f"系统：{source_player['name']} 抽取了 {target_player['name']} 1 张手牌。"})
        elif card_triggered == "江山易主":
            target_player["hand"], source_player["hand"] = source_player["hand"], target_player["hand"]
            socketio.emit('log', {"msg": f"系统：{source_player['name']} 与 {target_player['name']} 互换了全部手牌。"})
        game_state["pending_response"] = None

    if action_resolved and pending and pending.get("self_damage", 0) > 0:
        if cancelled_by_wall:
            socketio.emit('log', {"msg": f"系统：由于该牌已被【长城】无效，使用方 {source_player['name']} 免受自伤效果。"})
        else:
            sd = pending["self_damage"]
            source_player["hp"] -= sd
            socketio.emit('log', {"msg": f"系统：结算自伤效果，使用方 {source_player['name']} 失去 {sd} 点生命值。"})

    check_health_and_victory()
    broadcast_state()

@socketio.on('end_turn')
def handle_end_turn():
    if game_state["players"][game_state["current_idx"]]["sid"] != request.sid or game_state["pending_response"] or game_state["pending_beishui"] is not None: return
    current_player = game_state["players"][game_state["current_idx"]]
    
    for p in game_state["players"]:
        if p["alive"] and p.get("status_cooldown", 0) > 0:
            p["status_cooldown"] -= 1

    if current_player["alive"] and current_player["status"] == "饮鸩止渴":
        current_player["yinzhen_turns"] += 1
        if current_player["yinzhen_turns"] % 3 == 0:
            current_player["max_hp"] = max(1, current_player["max_hp"] - 2)
            current_player["hp"] = min(current_player["max_hp"], current_player["hp"] + 2)
            socketio.emit('log', {"msg": f"【饮鸩止渴】效果：已经过 3 回合，最大生命值减少 2 点，当前生命值恢复 2 点。"})
            
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
        socketio.emit('log', {"msg": f"系统：等待 {next_player['name']} 选择【背水一战】失去生命值的点数。"})

    broadcast_state()

if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)
