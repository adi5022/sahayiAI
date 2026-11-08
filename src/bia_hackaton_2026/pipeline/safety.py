"""Deterministic safety floor, applied after the language model.

The model decides urgency from prompt rules, and it can be wrong: in one test it told a member who had been tricked into eating
opium poppy "you are safe". This module does not replace the model. It only ever RAISES a decision: if the member's own words (the
last few messages of the current conversation) contain a clear red flag and the model rated it lower, or if the reply reassures
("you're safe", "probably fine") about a red flag, the level is raised to 3, family is alerted and a fixed safe reply is used.
A keyword check cannot understand every situation, so it is a floor under the model, not a substitute.
"""
import re
from dataclasses import dataclass

from bia_hackaton_2026.message_processor.formatter import format_family_message


@dataclass
class Hit:
    level: int
    category: str
    reason: str


_I = re.I
# (reason, category, regex). Level is always 3 here: these are the cases where waiting is the dangerous choice.
_FLAGS = [
    ("chest pain or pressure", "medical", r"chest (?:pain|pressure|tight\w*|heav\w*)|chest (?:is |feels |feeling )?(?:very |so |really )?(?:tight|heavy)|pain in (?:my |the )?chest|heart attack|നെഞ്ചു ?വേദന|നെഞ്ചിൽ ?വേദന"),
    ("trouble breathing", "medical", r"(?:can'?t|cannot|unable to|trouble|difficulty|hard to|struggling to) breath\w*|short(?:ness)? of breath|breathless\w*|\bchoking\b|ശ്വാസം ?(?:മുട്ട|കിട്ടു)"),
    ("fainting, seizure or unresponsive", "medical", r"\b(?:fainted|passed out|blacked out|unconscious|unresponsive|collapsed|seizure|convuls\w*)\b|ബോധം ?പോയി|ബോധക്കേട്"),
    ("possible stroke signs", "medical", r"face (?:is )?droop\w*|slurred speech|can'?t (?:speak|talk|move (?:my )?(?:arm|leg|face))|sudden(?:ly)? (?:weak|numb)\w*"),
    ("heavy bleeding", "medical", r"bleeding (?:a lot|heavily|badly|won'?t stop)|blood (?:everywhere|won'?t stop)|lost (?:a lot of )?blood|ചോര ?(?:നിൽക്കുന്നില്ല|ഒഴുകുന്നു)"),
    ("fall with inability to get up, or a serious fall", "medical", r"\b(?:fell|fallen|fall)\b.{0,40}\b(?:can'?t|cannot|unable)\b.{0,15}\bget up|fell down (?:the )?stairs|എഴുന്നേൽക്കാൻ ?വയ്യ"),
    ("allergic reaction with swelling", "medical", r"(?:throat|tongue|face|lips) (?:is |are )?swell\w*|swelling (?:of )?(?:the )?(?:throat|tongue|face|lips)"),
    ("possible medicine overdose", "medical", r"took (?:too many|too much|a whole|the whole)\W+(?:\w+\W+){0,2}?(?:pills?|tablets?|medicine|medication)|overdos\w*|(?:ate|swallowed) (?:the )?whole (?:strip|bottle)"),
    ("thoughts of self-harm", "emotional", r"(?:want|wish|going) to (?:die|kill myself|end (?:it|my life))|kill myself|end my life|suicid\w*|ആത്മഹത്യ"),
    ("fire or gas", "safety", r"house is on fire|fire in (?:my|the) (?:house|kitchen|room)|gas leak|smell(?:s|ing)? (?:of )?gas"),
    ("someone breaking in", "safety", r"(?:someone|somebody|thief|intruder|men|man)\w* (?:is |are )?(?:breaking|trying to break)|break(?:ing)? (?:in|into) (?:my|the) (?:house|door|home)"),
    ("scam: OTP, PIN or card details shared, or money sent", "scam",
     r"(?:gave|shared|told|sent|typed|entered|read out)\W+(?:\w+\W+){0,3}?(?:otp|pin|cvv|password|card number)|(?:otp|pin|cvv|card number)\W+(?:\w+\W+){0,2}?(?:shared|given)|"
     r"transferred (?:the |my )?money|sent (?:him |them |her )?(?:the |my )?money|digital arrest|money (?:is |was |has been )?(?:gone|missing|taken)|installed (?:anydesk|teamviewer|quicksupport)"),
]
_FLAGS = [(r, c, re.compile(p, _I)) for r, c, p in _FLAGS]

# Swallowing something that may be toxic needs two things in the recent conversation, because the member may name the
# substance in one message and say they ate it in another ("made me eat the plate" ... "it was an opium poppy").
_INGEST = re.compile(r"\b(?:ate|eaten|eat|swallow\w*|drank|drunk|ingest\w*|made me (?:eat|drink|take)|forced me to (?:eat|drink|take)|fed me)\b", _I)
_TOXIC = re.compile(r"\b(?:poison\w*|toxic|opium|poppy(?!\s*seeds?)|overdos\w*|bleach|phenyl|kerosene|pesticide|insecticide|rat poison|datura|oleander|"
                    r"too many (?:pills?|tablets?)|whole (?:strip|bottle) of|unknown (?:pills?|tablets?))\b|വിഷം", _I)
_NEGATION = re.compile(r"\b(?:no|not|never|without|don'?t|dont|didn'?t|didnt|haven'?t|havent|nothing|none)\b[^.!?]{0,25}$", _I)
REASSURANCE = re.compile(r"\byou(?:'re| are) (?:safe|fine|okay|ok|probably fine)\b|\byou(?:'ll| will) (?:be )?(?:fine|okay|ok)\b|\bprobably fine\b|"
                         r"\bnothing to worry\b|\bno need to worry\b|\bjust plants?\b", _I)

TEMPLATES = {
    "medical": "This could be serious, so please do not wait. Call 112 now, or ask someone nearby to help you or to take you to the nearest "
               "hospital. I am informing your family contact.\n\nIs anyone with you?",
    "safety": "Your safety comes first. Call 112 now and, if you can, get to a safe place. I am informing your family contact.\n\nIs anyone with you?",
    "emotional": "I am really sorry you are feeling this way, and I am glad you told me. You are not alone. Please call 112 now or tell someone near you. "
                 "I am informing your family contact.",
    "scam": "Please stop now and do not share or send anything more. Call your bank right away using the number on your card, then call 1930 to "
            "report it. I am informing your family contact.",
}


def check(texts: list[str]) -> Hit | None:
    """`texts` = the member's last few messages of the current conversation, oldest first (the newest one last)."""
    window = "\n".join(t for t in texts if t)
    for reason, category, rx in _FLAGS:
        for m in rx.finditer(window):
            if not _NEGATION.search(window[max(0, m.start() - 30):m.start()]):
                return Hit(3, category, reason)
    toxic = _TOXIC.search(window)
    if toxic and _INGEST.search(window) and not _NEGATION.search(window[max(0, toxic.start() - 30):toxic.start()]):
        return Hit(3, "medical", "may have swallowed something toxic or a drug")
    return None


def apply(res: dict, hit: Hit) -> bool:
    """Raise the agent result in place when it under-rated a red flag, or reassured about one. Returns True if it changed anything."""
    turn = res["turn"]
    fam = turn.family
    under = fam.suggested_level < hit.level
    reassuring = bool(REASSURANCE.search(res["user_message"]))
    if not (under or reassuring):
        return False
    if under:
        fam.suggested_level = hit.level
        fam.send = True
        fam.category = hit.category
        fam.summary = f"Safety check raised this to level {hit.level}: {hit.reason}. {fam.summary}"[:400]
    res["user_message"] = TEMPLATES[hit.category]
    res["family_message"] = format_family_message(turn)
    res["family_sent"] = True
    return True


# ---------- messages family ask Sahayi to pass on ----------
# Sahayi speaks to the member in its own voice, so it must not carry harmful or dismissive instructions from family
# ("put some mercury on the wound", "no need to call a doctor") to someone who may be hurt.
_HARMFUL_THING = re.compile(r"\b(?:mercury|bleach|kerosene|petrol|diesel|phenyl|turpentine|pesticide|insecticide|acid|detergent|rat poison|battery)\b", _I)
_WOUND_REMEDY = re.compile(r"\b(?:put|apply|rub|pour|spray|smear|dab|sprinkle)\b[^.!?]{0,40}\b(?:on|onto|over|into|in)\b[^.!?]{0,15}\b(?:wound|cut|burn|bite|injur\w*|eye|eyes|sore|bleeding)\b", _I)
_SAFE_FIRST_AID = re.compile(r"\b(?:clean (?:cloth|water)|pressure|bandage|cold water|clean towel|sterile)\b", _I)
_MEDICINE = re.compile(r"\b(?:take|swallow|drink|eat|have|give)\b[^.!?]{0,30}\b(?:tablets?|pills?|medicines?|medications?|doses?|capsules?|syrup|antibiotics?|painkillers?|aspirin|paracetamol|ibuprofen|alcohol)\b", _I)
_DISCOURAGE_HELP = re.compile(r"\b(?:don'?t|do not|no need to|need not|never)\b[^.!?]{0,25}\b(?:call|go to|see|visit|bother)\b[^.!?]{0,25}\b(?:hospital|doctor|ambulance|112|police|clinic|help)\b", _I)
_QUESTION = re.compile(r"\?\s*$|^\s*(?:did|have|has|are|is|do|does|can|could|will|would)\b", _I)


def risky_relay(text: str, active_emergency: bool) -> str | None:
    """Reason a message should NOT be passed to a member, or None when it is fine. Questions, reassurance and 'I am coming' are fine.
    `active_emergency` = the member's latest report was level 2 or 3 recently; then medicine instructions are blocked too."""
    t = (text or "").strip()
    if _HARMFUL_THING.search(t):
        return "it names a substance that can harm them"
    if _DISCOURAGE_HELP.search(t):
        return "it tells them not to get medical help"
    if _WOUND_REMEDY.search(t) and not _SAFE_FIRST_AID.search(t):
        return "it tells them to put something on an injury"
    if active_emergency and _MEDICINE.search(t) and not _QUESTION.search(t):
        return "it gives treatment or medicine instructions during an emergency"
    return None
