import random
import time
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.config['SECRET_KEY'] = 'sg_ultimate_match_2026_fixed'
socketio = SocketIO(app, cors_allowed_origins="*")

# ==========================================
# 🃏 200张标准化卡池（已重构）
# 攻略：攻击略多于防御
# ==========================================
BASIC_CARDS = (
    ["攻"] * 60 +
    ["防"] * 50 +
    ["长城"] * 12 +
    ["回血"] * 20 +
    ["卡牌大师"] * 15 +
    ["荆轲刺秦"] * 10 +
    ["一字马"] * 10 +
    ["顺手牵羊"] * 10 +
    ["江山易主"] * 5 +
    ["同归于尽"] * 8
)

STATUS_CARDS = ["背水一战", "饮鸩止渴", "卧薪尝胆", "暗度陈仓"]


# ==========================================
# 🎮 游戏核心结构重构
# ==========================================
class GameEngine:
    def __init__(self):
        self.bot_count = 2
        self.reset_all()

    def reset_all(self):
        self.active = False
        self.players = []
        self.current_idx = 0

        # 当前“回合”（玩家轮次）
        self.turn = 1

        # 当前轮（所有人走完才+1）
        self.round = 1

        # 行动力系统（关键修改）
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
# ⚙️ 基础工具函数
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
    if count <= 0:
        return

    p = game.players[player_idx]
    drawn = []

    for _ in range(count):
        if not game.deck:
            rebuild_decks()
            add_log("♻️ 牌库耗尽，重新洗牌！")

        if game.deck:
            drawn.append(game.deck.pop(0))

    p['hand'].extend(drawn)


def force_hp_limit(player):
    if player['hp'] > player['max_hp']:
        player['hp'] = player['max_hp']


# ==========================================
# 🤖 AI思考时间（预埋：后面用）
# ==========================================
def ai_think():
    """
    AI思考延迟（2秒）
    后续 bot 行为都会调用
    """
    time.sleep(2)
# ==========================================
# 🎮 游戏启动（修复行动力 + 首回合BUG）
# ==========================================
def start_game_engine():
    game.active = True
    game.round = 1
    game.turn = 1
    game.logs = []

    rebuild_decks()

    factions = ["司", "冀", "丁"]
    random.shuffle(factions)

    # 初始化玩家
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

        p['faction_revealed'] = (p['faction'] == "冀")

        p['hand'] = [game.deck.pop(0) for _ in range(5)]
        p['status_cards'] = [game.status_deck.pop(0) for _ in range(2)]

    # 冀先手
    start_turn(0)


# ==========================================
# 🔁 修复：回合开始逻辑（核心）
# ==========================================
def start_turn(idx):
    if not game.active:
        return

    p = game.players[idx]

    if not p['alive']:
        next_turn()
        return

    game.current_idx = idx

    # ✅ 行动力修复：x+1机制
    game.actions_left = game.round + 1

    p['beishui_decided'] = False

    # 一字马跳过
    if p.get('skipped', False):
        p['skipped'] = False
        add_log(f"⏰ 【{p['name']}】被控制跳过回合")
        next_turn()
        return

    # ======================================
    # ❗ 修复：补牌机制（只在“新一轮”执行）
    # ======================================
    if idx == 0:
        for pl in game.players:
            if pl['alive']:
                need = 5 - len(pl['hand'])
                if need > 0:
                    draw_cards(pl['idx'], need)
        add_log("🔄 新一轮开始，全员补牌至5张")

    add_log(f"🎬 【{p['name']}】回合开始（行动力：{game.actions_left}）")

    trigger_bot_if_needed()


# ==========================================
# 🔁 修复：结束当前玩家回合
# ==========================================
def end_turn_logic():
    if not game.active:
        return

    add_log(f"🏁 【{game.players[game.current_idx]['name']}】结束回合")

    next_turn()


# ==========================================
# 🔁 修复：轮次推进（真正的一轮=全员走完）
# ==========================================
def next_turn():
    if not game.active:
        return

    start_idx = game.current_idx
    next_idx = start_idx

    attempts = 0

    while attempts < len(game.players):
        next_idx = (next_idx + 1) % len(game.players)

        if game.players[next_idx]['alive']:
            break

        attempts += 1

    # 防止死循环
    if next_idx == start_idx:
        return

    # ======================================
    # ❗ 一轮结束判断（所有人都走过一遍）
    # ======================================
    if next_idx == 0:
        game.round += 1
        add_log(f"🌙 ===== 第 {game.round} 轮开始 =====")

    start_turn(next_idx)
# ==========================================
# ⚔️ 核心出牌逻辑（重构版）
# ==========================================
def execute_play_card(src_idx, card, tgt_idx):
    src = game.players[src_idx]

    # 防御性检查
    if game.actions_left <= 0:
        return False

    if card not in src['hand']:
        return False

    game.actions_left -= 1
    src['hand'].remove(card)

    # ======================================
    # 卧薪尝胆（修复：不会自杀）
    # ======================================
    if src['status'] == "卧薪尝胆":
        if src['hp'] > 1:
            src['hp'] -= 1
            add_log(f"⚠️ 【卧薪尝胆】反噬：{src['name']} 扣1血（最低保留1）")

    tgt = game.players[tgt_idx] if tgt_idx != -1 else None

    # ======================================
    # 广播优化（回血/卡牌大师无目标提示）
    # ======================================
    if card in ["回血", "卡牌大师"]:
        add_log(f"🃏 【{src['name']}】使用【{card}】")
    else:
        add_log(f"🃏 【{src['name']}】使用【{card}】 ➜ 【{tgt['name'] if tgt else '无'}】")

    # ======================================
    # 卡牌效果
    # ======================================
    if card == "回血":
        src['hp'] += 1
        force_hp_limit(src)

    elif card == "卡牌大师":
        draw_cards(src_idx, 2)

    elif card == "攻":
        set_attack_pipeline(src_idx, tgt_idx, "攻", 1)

    elif card == "荆轲刺秦":
        set_attack_pipeline(src_idx, tgt_idx, "荆轲刺秦", 2)
        damage_player(src_idx, 1, reason="荆轲反噬")

    elif card == "一字马":
        tgt['skipped'] = True

    elif card == "顺手牵羊":
        if tgt and tgt['hand']:
            stolen = random.choice(tgt['hand'])
            tgt['hand'].remove(stolen)
            src['hand'].append(stolen)

    elif card == "江山易主":
        src['hand'], tgt['hand'] = tgt['hand'], src['hand']

    elif card == "同归于尽":
        damage_player(src_idx, 1, reason="同归于尽")
        damage_player(tgt_idx, 1, reason="同归于尽")

    check_victory_conditions()
    broadcast_state()
    return True


# ==========================================
# 🧠 暗度陈仓（彻底重写）
# ==========================================
def equip_status_logic(idx, status_card):
    p = game.players[idx]

    p['status'] = status_card
    p['status_cooldown'] = 3

    add_log(f"⚡ 【{p['name']}】装备状态：【{status_card}】")

    # ======================================
    # 新规则修复
    # ======================================
    if status_card == "饮鸩止渴":
        p['max_hp'] = 10
        p['hp'] = 10

    elif status_card in ["卧薪尝胆", "背水一战"]:
        p['max_hp'] = 5

    elif status_card == "暗度陈仓":
        p['max_hp'] = 5
        # 标记状态能力
        p['ad_mode'] = True

    force_hp_limit(p)


# ==========================================
# 💀 饮鸩止渴（修复版：不再回血）
# ==========================================
def apply_poison_tick(player):
    if player['status'] != "饮鸩止渴":
        return

    player['status_cooldown'] -= 1

    if player['status_cooldown'] <= 0:
        player['max_hp'] -= 2
        player['hp'] = min(player['hp'], player['max_hp'])
        player['status_cooldown'] = 3

        add_log(f"☠️ 【饮鸩止渴】毒发：{player['name']} 最大生命-2")


# ==========================================
# 🔥 AI思考接入（真正生效）
# ==========================================
def trigger_bot_if_needed():
    if not game.active or game.pending_action:
        return

    curr = game.players[game.current_idx]

    if curr.get('is_bot'):
        ai_think()  # ⭐ 2秒思考时间

        if curr['status'] == "背水一战" and not curr['beishui_decided']:
            handle_bot_beishui(game.current_idx)
            return

        run_bot_active_move(game.current_idx)
# ==========================================
# 💥 伤害系统（丁复活 + 死亡修复）
# ==========================================
def damage_player(idx, amount, reason=""):
    if amount <= 0:
        return

    p = game.players[idx]
    p['hp'] -= amount

    add_log(f"💥 【{p['name']}】受到【{reason}】伤害 -{amount}")

    # ======================================
    # 丁复活机制
    # ======================================
    if p['hp'] <= 0 and p['faction'] == "丁" and not p.get('has_revived', False):
        p['has_revived'] = True
        p['faction_revealed'] = True
        p['hp'] = 2
        p['max_hp'] = max(p['max_hp'], 2)

        draw_cards(idx, 2)
        add_log(f"🔥 【丁阵营复活】{p['name']} 重生（2血+2牌）")
        return

    # ======================================
    # 普通死亡
    # ======================================
    if p['hp'] <= 0:
        p['hp'] = 0
        p['alive'] = False
        p['hand'] = []
        p['status_cards'] = []
        p['status'] = "正常"

        add_log(f"☠️ 【阵亡】{p['name']} 已出局（阵营：{p['faction']}）")


# ==========================================
# 🛡️ 防御结算优化
# ==========================================
def set_attack_pipeline(src_idx, tgt_idx, card, count):
    tgt = game.players[tgt_idx]

    # 长城秒防
    if "长城" in tgt['hand']:
        tgt['hand'].remove("长城")
        add_log(f"🧱 【{tgt['name']}】使用长城完全格挡")
        return

    game.pending_action = {
        "source_idx": src_idx,
        "target_idx": tgt_idx,
        "card": card,
        "required_defenses": count
    }

    # AI自动防御
    if tgt.get('is_bot'):
        handle_bot_defense_response(tgt_idx)


# ==========================================
# ⚔️ 胜负判定（修复稳定版）
# ==========================================
def check_victory_conditions():
    if not game.active:
        return

    si_alive = any(p['alive'] and p['faction'] == "司" for p in game.players)
    ji_alive = any(p['alive'] and p['faction'] == "冀" for p in game.players)

    if not ji_alive:
        game.active = False
        add_log("🏆 司阵营胜利（冀全灭）")
        return

    if not si_alive:
        game.active = False
        add_log("🏆 冀/丁联盟胜利（司全灭）")
        return


# ==========================================
# 📡 broadcast_state 安全修复（防崩）
# ==========================================
def broadcast_state():
    if not game.active:
        return

    for p in game.players:
        if p.get('is_bot'):
            continue

        sid = p['sid']

        client_players = []
        for x in game.players:
            client_players.append({
                "name": x['name'],
                "idx": x['idx'],
                "alive": x['alive'],
                "hp": x['hp'],
                "max_hp": x['max_hp'],
                "faction": x['faction'] if x['faction_revealed'] or not x['alive'] else "隐藏",
                "hand_count": len(x['hand']),
                "status": x['status'],
                "status_cooldown": x['status_cooldown']
            })

        socketio.emit('game_update', {
            "round": game.round,
            "turn": game.turn,
            "current_idx": game.current_idx,
            "actions_left": game.actions_left,
            "players": client_players,
            "my_cards": p['hand'],
            "my_status_cards": p['status_cards'],
            "my_status": p['status'],
            "my_idx": p['idx']
        }, to=sid)


# ==========================================
# 🤖 bot防御逻辑稳定版
# ==========================================
def handle_bot_defense_response(bot_idx):
    if not game.pending_action:
        return

    p = game.players[bot_idx]

    while game.pending_action and game.pending_action['required_defenses'] > 0:

        if "长城" in p['hand']:
            p['hand'].remove("长城")
            add_log(f"🧱 AI【{p['name']}】长城防御")
            game.pending_action = None
            return

        if "防" in p['hand']:
            p['hand'].remove("防")
            game.pending_action['required_defenses'] -= 1
            add_log(f"🛡️ AI【{p['name']}】防御成功")

        elif p.get('ad_mode') and "攻" in p['hand']:
            p['hand'].remove("攻")
            game.pending_action['required_defenses'] -= 1
            add_log(f"🎭 AI【{p['name']}】暗度陈仓以攻代防")

        else:
            # 无法防御 → 承受伤害
            dmg = 2 if game.pending_action['card'] == "荆轲刺秦" else 1
            damage_player(bot_idx, dmg, reason=game.pending_action['card'])
            game.pending_action = None
            break

    if game.pending_action and game.pending_action['required_defenses'] <= 0:
        game.pending_action = None

    check_victory_conditions()
    broadcast_state()


# ==========================================
# 🧠 暗度陈仓规则最终版（核心修复）
# ==========================================
def dark_mode_attack_swap(player, card_type):
    """
    card_type:
    - "攻"
    - "防"
    """

    if player['status'] != "暗度陈仓":
        return False

    # 出牌方
    if game.current_idx == player['idx']:
        # 只能 防 → 攻（选择性）
        return card_type == "防"

    # 防守方
    else:
        # 只能 攻 → 防（选择性）
        return card_type == "攻"


# ==========================================
# 🚀 Flask启动
# ==========================================
if __name__ == '__main__':
    socketio.run(app, debug=True, port=5000)
