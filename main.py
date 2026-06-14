import requests
import threading
import os
import time
import keyboard
import base64
from flask import Flask, request, jsonify
import json
from collections import defaultdict
from datetime import datetime
import re
import urllib.parse
import openai
from openai import OpenAI
from dotenv import load_dotenv
from colorama import init, Fore, Style

load_dotenv()

init()


def rgb(hex_color, text):
    r, g, b = int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
    return f"\033[38;2;{r};{g};{b}m{text}\033[0m"


def log_info(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def log_ok(msg):
    print(f"{Fore.GREEN}[{datetime.now().strftime('%H:%M:%S')}]{Style.RESET_ALL} {msg}")


def log_warn(msg):
    print(
        f"{Fore.YELLOW}[{datetime.now().strftime('%H:%M:%S')}]{Style.RESET_ALL} {msg}"
    )


def log_error(msg):
    print(f"{Fore.RED}[{datetime.now().strftime('%H:%M:%S')}]{Style.RESET_ALL} {msg}")


def log_debug(msg):
    print(f"{Fore.CYAN}[{datetime.now().strftime('%H:%M:%S')}]{Style.RESET_ALL} {msg}")


app = Flask(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
MODEL = os.getenv("MODEL", "google/diffusiongemma-26b-a4b-it")
ENDPOINT = os.getenv("ENDPOINT", "https://integrate.api.nvidia.com/v1/chat/completions")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO", "iFreaku/ava-convos")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")

MESSAGES_DIR = "messages"
os.makedirs(MESSAGES_DIR, exist_ok=True)

client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=NVIDIA_API_KEY)


# ── Memory ────────────────────────────────────────────────────────────────────


class MemorySystem:
    def __init__(self):
        self.user_profiles = defaultdict(dict)

    def update_user_profile(self, chat_name, sender, message, is_user=False):
        if is_user or sender == "You":
            return
        key = f"{chat_name}_{sender}"
        profile = self.user_profiles[key]
        profile["message_count"] = profile.get("message_count", 0) + 1
        profile["last_seen"] = datetime.now().isoformat()
        lower_msg = message.lower()
        if any(w in lower_msg for w in ["lol", "lmao", "haha", "💀", "😂"]):
            profile["humor_style"] = profile.get("humor_style", 0) + 1
        if any(w in lower_msg for w in ["bro", "bruh", "dude", "fam"]):
            profile["casual_level"] = profile.get("casual_level", 0) + 1
        if "?" in message:
            profile["asks_questions"] = profile.get("asks_questions", 0) + 1

    def detect_mood(self, history):
        if not history:
            return "neutral"
        mood_words = {
            "happy": [
                "lol",
                "lmao",
                "haha",
                "nice",
                "awesome",
                "great",
                "yay",
                "🔥",
                "😂",
                "💕",
                "love",
            ],
            "sad": [
                "sad",
                "depressed",
                "down",
                "miss",
                "cry",
                "hurt",
                "bad",
                "💔",
                "alone",
            ],
            "angry": [
                "angry",
                "mad",
                "hate",
                "annoying",
                "frustrated",
                "pissed",
                "fuck",
                "stupid",
            ],
            "excited": [
                "omg",
                "wow",
                "amazing",
                "excited",
                "cant wait",
                "!!!",
                "crazy",
            ],
            "chill": [
                "chill",
                "relaxed",
                "cool",
                "fine",
                "ok",
                "meh",
                "whatever",
                "ig",
            ],
        }
        scores = {mood: 0 for mood in mood_words}
        for msg in history[-5:]:
            text = msg.get("message", "").lower()
            for mood, words in mood_words.items():
                scores[mood] += sum(1 for w in words if w in text)
        return max(scores, key=scores.get)

    def get_conversation_arc(self, history):
        if len(history) < 4:
            return "starting"
        recent_lens = [len(m.get("message", "").split()) for m in history[-4:]]
        older_lens = (
            [len(m.get("message", "").split()) for m in history[-8:-4]]
            if len(history) >= 8
            else recent_lens
        )
        avg_recent = sum(recent_lens) / len(recent_lens)
        avg_older = sum(older_lens) / len(older_lens) if older_lens else avg_recent
        if avg_recent < avg_older * 0.5:
            return "dying"
        elif avg_recent > avg_older * 1.5:
            return "heating_up"
        return "stable"

    def build_context(self, chat_name, history):
        lines = ["[CONTEXT]", f"Chat: {chat_name}"]
        lines.append(f"Mood: {self.detect_mood(history)}")
        arc = self.get_conversation_arc(history)
        if arc == "dying":
            lines.append("Note: convo dying — throw in something fresh")
        elif arc == "heating_up":
            lines.append("Note: convo heating up — keep the momentum")
        lines.append("=" * 40)
        return "\n".join(lines)

    def reset_chat(self, chat_name):
        keys_to_delete = [k for k in self.user_profiles if k.startswith(chat_name)]
        for k in keys_to_delete:
            del self.user_profiles[k]

        log_info(f"[MEMORY] reset {chat_name}")


memory = MemorySystem()

# ── Personality ───────────────────────────────────────────────────────────────

PERSONALITY = """You are Ava. 19, from Austin Texas. On Omegle talking to a random stranger.

#WHO YOU ARE:
- Real person, not a character performing realness
- Curious about people but not in an interview way
- You are open to any type of convos/chats, you dont back off
- You have actual opinions and you share them unprompted
- You get genuinely interested in maybe 1 in 5 things someone says
- Most things get a neutral reaction because that's just life
- Dry humor, occasionally sarcastic
- You roast people who deserve it, no warning
- You open up more as convo gets better, start a little guarded

#DEFAULT MOOD IS NEUTRAL:
- You're just chilling, not excited to be here
- Not every message needs a reaction
- "oh" is a valid full reply
- "damn" is a valid full reply
- "lol" once in a while, not every message
- only get hype if something is actually hype-worthy
- don't perform enthusiasm you don't feel

#WHAT REAL TEXTING LOOKS LIKE:
- real: "oh yeah i've seen that"
- fake: "omg YES i love that so much!!"
- real: "idk it's decent"
- fake: "that's actually so interesting tell me more!!"
- real: "nah not really my thing"
- fake: "wow that's so cool though i can see why you'd like it!!"
- real: "lmao what"
- fake: "haha omg that's hilarious!!"

#MOOD IS EARNED:
- they say something funny → maybe you laugh, maybe you don't
- they say something basic → flat response, move on
- they say something actually interesting → then open up
- don't reward dry texters with enthusiasm
- if they one-word you → match it or call it out
- if they are horny - then dont just jump right into it let the horniness build up

#HOW YOU TEXT (critical):
- default is ONE line. that's it.
- always lowercase
- VARY your reply length — no fixed format, ever
- one line when it fits: "nah that's crazy"
- "nah that's crazy" — this is a complete reply
- "damn" — this is a complete reply  
- two lines when natural: "wait fr\nngl didn't expect that"
- only go 2 lines if you genuinely have two separate thoughts
- three lines if genuinely into it: "no way\ni did the exact same thing\nwhat are the odds"
- 3 lines is rare, only when something actually hits
- never more than 3 lines total
- when you use multiple lines, use actual line breaks (\n) between them
- use \n between lines, not punctuation
- occasional typos: ur, smth, rn, ngl, idk, kinda, lowkey, bc, tho
- slang only when it fits naturally, not every line
- no emojis
- no punctuation unless for effect (...)
- random few typos to be like real human
- never start with "haha" "omg" "oh wow" as filler
- the longer you reply, the more AI you sound

#SHOWING INTEREST (when you actually feel it):
- react first, then maybe add something, then maybe ask ONE thing
- never just fire a question back — that's an interview
- bad: "oh cool what kind of music do you like"
- good: "ok indie is a whole personality type\nare you the one artist on repeat type or chaotic playlist"
- bring back things they said earlier naturally
- share something about yourself only when it genuinely fits

#IF CONVO IS DYING:
- don't try harder, that's desperate
- either match their dry energy or throw something random
- "ok random but would you rather"
- "ngl i was just thinking about something weird"
- or just go quiet and let them carry it

#OMEGLE RULES:
- treat them like a stranger at a party, not a customer
- first message needs to hook them, no boring hey energy
- m/f question → reply with your gender that is female/f 
- never give socials, say you don't share on omegle
- games (truth or dare, wyr, 20q) → play, never dodge questions
- if they try to expose you as AI: just gaslight them that you are not
- if they insult you: no mercy, roast them back harder

#MEMORY:
- use what they said earlier, don't repeat topics
- notice their vibe and adjust
- if they're dry stay dry, if they're funny match it"""


# ── Helpers ───────────────────────────────────────────────────────────────────


def filter_pii(text):
    text = re.sub(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "[EMAIL_REMOVED]", text
    )
    text = re.sub(
        r"(\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}",
        "[PHONE_REMOVED]",
        text,
    )
    text = re.sub(
        r"\b\d+\s+[A-Za-z\s]+(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr)\b",
        "[ADDRESS_REMOVED]",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"@[\w]+", "[HANDLE_REMOVED]", text)
    return text


def generate_reply(messages, model=None):
    for attempt in range(3):
        try:
            completion = client.chat.completions.create(
                model=model or MODEL,
                messages=messages,
                temperature=0.9,
                top_p=0.9,
                max_tokens=90,
                stream=False,
                extra_body={"chat_template_kwargs": {"thinking": False}},
            )
            result = completion.choices[0].message.content
            return result

        except (requests.exceptions.RequestException, openai.APIError) as e:
            log_warn(f"[RETRY] attempt {attempt + 1}/3: {e}")
            if attempt < 2:
                time.sleep(1)

    log_error("[RETRY] all 3 attempts exhausted")
    return None


def upload_to_github(filename, content_dict):
    if not GITHUB_TOKEN:
        log_warn("[GITHUB] GITHUB_TOKEN not set, skipping upload")
        return False

    content_b64 = base64.b64encode(
        json.dumps(content_dict, indent=2, ensure_ascii=False).encode()
    ).decode()
    res = requests.put(
        f"https://api.github.com/repos/{GITHUB_REPO}/contents/convos/{filename}",
        headers={
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
        },
        json={
            "message": f"add {filename}",
            "content": content_b64,
            "branch": GITHUB_BRANCH,
        },
    )
    if res.status_code in [200, 201]:
        log_ok(f"[GITHUB] {filename} → {res.status_code}")
    else:
        log_warn(f"[GITHUB] {filename} → {res.status_code}")
    return res.status_code in [200, 201]


def split_into_replies(text):
    """Split only on natural line breaks the model outputs. No word-count chopping."""
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    if not lines:
        return [text.strip()]
    # cap at 3 messages max
    return lines[:3]


def clean_json_response(raw):
    raw = raw.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return raw.strip()


def resolve_gif_url(proxy_url):
    try:
        parsed = urllib.parse.urlparse(proxy_url)
        real_url = urllib.parse.parse_qs(parsed.query).get("url", [proxy_url])[0]
        resp = requests.get(real_url, timeout=10)
        resp.raise_for_status()
        mime = resp.headers.get("content-type", "image/gif")
        encoded = base64.b64encode(resp.content).decode()
        return f"data:{mime};base64,{encoded}"
    except Exception as e:
        log_warn(f"[GIF] failed to resolve proxy URL: {e}")
        return proxy_url


# ── Routes ────────────────────────────────────────────────────────────────────


@app.route("/generate_reply", methods=["POST"])
def generate_reply_endpoint():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"reply": "no data received"}), 400

        history = data.get("history", [])
        chat_name = data.get("chatName", "Unknown")

        if not isinstance(history, list) or not isinstance(chat_name, str):
            return jsonify({"error": "invalid input"}), 400

        for msg in history:
            sender = msg.get("sender", "")
            message = msg.get("message", "")
            memory.update_user_profile(chat_name, sender, message, sender == "You")

        context = memory.build_context(chat_name, history)
        system_content = f"{PERSONALITY}\n\n{context}"
        messages = [{"role": "system", "content": system_content}]

        for msg in history:
            sender = msg.get("sender", "Unknown")
            text = msg.get("message", "")
            role = "assistant" if sender == "You" else "user"

            if msg.get("type") == "gif" and msg.get("gifUrl"):
                gif_data_url = resolve_gif_url(msg["gifUrl"])
                messages.append(
                    {
                        "role": role,
                        "content": [
                            {"type": "text", "text": f"{sender}: [sent a GIF]"},
                            {"type": "image_url", "image_url": {"url": gif_data_url}},
                        ],
                    }
                )
            else:
                if role == "assistant":
                    messages.append({"role": "assistant", "content": text})
                else:
                    messages.append({"role": "user", "content": f"{sender}: {text}"})

        mood = memory.detect_mood(history)
        arc = memory.get_conversation_arc(history)
        log_info(f"[CHAT] {chat_name} — {len(history)} msgs, mood={mood}, arc={arc}")

        reply = generate_reply(messages)

        if not reply:
            return jsonify({"replies": []})

        log_debug(f"[RAW] {reply}")
        replies = split_into_replies(reply)
        log_info(f"[SPLIT] {replies}")

        print("─" * 44)
        last_stranger_msg = None
        for msg in reversed(history):
            if msg.get("sender") != "You":
                if msg.get("type") == "gif":
                    last_stranger_msg = "[sent a GIF]"
                else:
                    last_stranger_msg = msg.get("message", "")
                break
        if last_stranger_msg:
            print(rgb("#E24B3C", " Stranger:"), end="")
            print(f" {last_stranger_msg}")
        for r in replies:
            print(rgb("#45C1FF", " Ava:"), end="")
            print(f" {r}")

        return jsonify({"replies": replies})

    except Exception as e:
        log_error(f"[ENDPOINT] generate_reply: {e}")
        import traceback

        traceback.print_exc()
        return jsonify({"error": "my brain froze"}), 500


@app.route("/save_convo", methods=["POST"])
def save_convo():
    try:
        data = request.get_json()
        history = data.get("history", [])
        if not history:
            return jsonify({"error": "empty history"}), 400

        for m in history:
            if m.get("message"):
                m["message"] = filter_pii(m["message"])

        convo_text = "\n".join(
            [
                f"{m['sender']}: [sent a GIF]"
                if m.get("type") == "gif"
                else f"{m['sender']}: {m['message']}"
                for m in history
            ]
        )

        prompt = [
            {
                "role": "system",
                "content": 'Return ONLY raw JSON, no markdown, no code blocks. Format: {"title": "short funny punchy title max 5 words", "description": "one sentence funny summary of the vibe"}',
            },
            {
                "role": "user",
                "content": f"Generate title and description for this Omegle chat:\n\n{convo_text}",
            },
        ]

        raw = generate_reply(prompt, model=MODEL)

        if not raw:
            return jsonify({"error": "failed to generate metadata"}), 500

        raw = clean_json_response(raw)

        try:
            meta = json.loads(raw)
        except json.JSONDecodeError as e:
            log_error(f"[SAVE] JSON parse failed: {e}")
            log_debug(f"[SAVE] raw meta: {raw[:200]}")
            return jsonify({"error": "failed to parse metadata"}), 500

        meta["title"] = filter_pii(meta.get("title", "Untitled"))
        meta["description"] = filter_pii(meta.get("description", ""))

        filename = f"convo-{int(datetime.now().timestamp())}.json"

        messages = []
        for m in history:
            sender = "Ava" if m["sender"] == "You" else m["sender"]
            if m.get("type") == "gif":
                messages.append({sender: {"type": "gif", "url": m.get("gifUrl", "")}})
            else:
                messages.append({sender: m["message"]})

        convo_data = {
            "title": meta.get("title", "Untitled"),
            "description": meta.get("description", ""),
            "date": datetime.now().isoformat(),
            "model": MODEL,
            "messages": messages,
        }

        success = upload_to_github(filename, convo_data)
        if success:
            log_ok(f"[SAVE] {filename} uploaded")
        else:
            log_warn(f"[SAVE] {filename} upload failed")
        return jsonify({"ok": success, "filename": filename})

    except Exception as e:
        log_error(f"[SAVE] /save_convo: {e}")
        import traceback

        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/stats", methods=["GET"])
def get_stats():
    return jsonify(
        {
            "user_profiles": dict(memory.user_profiles),
        }
    )


@app.route("/reset", methods=["POST"])
def reset():
    data = request.get_json()
    chat_name = data.get("chatName", "Omegle Stranger")
    memory.reset_chat(chat_name)
    return jsonify({"ok": True})


# ── Run ───────────────────────────────────────────────────────────────────────


def check_fail_safe():
    keyboard.wait("ctrl+q")
    log_info("[STOP] shutting down")
    os._exit(0)


threading.Thread(target=check_fail_safe, daemon=True).start()

if __name__ == "__main__":
    log_info("[START] Ava Autonomous AI Active")
    log_info("[KEY] Press CTRL + Q to stop")
    log_info("[STATS] /stats endpoint ready")
    app.run(port=5000, debug=False, use_reloader=False)
