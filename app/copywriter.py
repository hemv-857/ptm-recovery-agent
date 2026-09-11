"""Deterministic Hinglish nudge copy. Transactional recovery messages only,
with opt-out footer (compliance). Optional LLM polish when configured.

Tier 2/3 India optimized — conversational, friendly, not corporate.
"""
from __future__ import annotations

from .llm import chat_json
from .models import ActionType, FailureClass, RecoveryCase

FOOTER = "\n\n(Ye payment update hai. STOP reply karke opt out kar sakte hain.)"

_TEMPLATES: dict[tuple[FailureClass, str], str] = {
    (FailureClass.INSUFFICIENT_FUNDS, "whatsapp"):
        "Namaste {name}! Aapka payment Rs {amt} balance kam hone ki wajah se fail hua tha. "
        "Wallet mein paisa daal ke 1 tap me complete karein: {link}",
    (FailureClass.INSUFFICIENT_FUNDS, "sms"):
        "{name}, Rs {amt} payment fail tha (low balance). Pay here: {link}",
    (FailureClass.NETWORK_TIMEOUT, "whatsapp"):
        "Hi {name}, network issue ki wajah se Rs {amt} payment incomplete raha. "
        "Aapka order reserved hai — yahan complete karein: {link}",
    (FailureClass.NETWORK_TIMEOUT, "sms"):
        "{name}, Rs {amt} payment network issue se fail hua. Retry karein: {link}",
    (FailureClass.ISSUER_UNAVAILABLE, "sms"):
        "{name}, bank issue se Rs {amt} payment fail hua tha. Retry karein: {link}",
    (FailureClass.SOFT_DECLINE_OTHER, "whatsapp"):
        "Hi {name}, aapka bank ne Rs {amt} transaction decline kiya. "
        "Kripya apna bank app check karein ya dusra tareeka try karein: {link}",
    (FailureClass.HARD_DECLINE, "whatsapp"):
        "Hi {name}, card decline ho gaya. UPI ya dusre instrument se Rs {amt} "
        "yahan pay karein: {link}",
    (FailureClass.MANDATE_ISSUE, "whatsapp"):
        "Hi {name}, aapka auto-debit mandate inactive ho gaya hai. Service continue "
        "rakhne ke liye 1 minute me re-authorize karein: {link}",
    (FailureClass.CUSTOMER_ABANDONMENT, "whatsapp"):
        "Hi {name}, aapka Rs {amt} ka order abhi bhi wait kar raha hai! "
        "Checkout complete karein: {link}",
    (FailureClass.CUSTOMER_ABANDONMENT, "sms"):
        "{name}, your Rs {amt} order is waiting. Complete checkout: {link}",
    (FailureClass.INVOICE_OVERDUE, "whatsapp"):
        "Hello {name}, invoice of Rs {amt} is pending since {days} din. "
        "Pay securely here: {link}",
    (FailureClass.INVOICE_OVERDUE, "sms"):
        "{name}: Invoice Rs {amt} overdue by {days} days. Pay: {link}",
    (FailureClass.INVOICE_OVERDUE, "email"):
        "Dear {name}, our records show invoice Rs {amt} is {days} days overdue. "
        "Kindly clear it here at the earliest: {link}",
    (FailureClass.SUBSCRIPTION_FAILED, "whatsapp"):
        "Hi {name}, aapki Rs {amt} ki auto-renewal fail ho gayi. Service continue "
        "rakhne ke liye payment complete karein: {link}",
    (FailureClass.SUBSCRIPTION_FAILED, "email"):
        "Hi {name}, we couldn't renew your plan (Rs {amt}). Update your payment "
        "method or manage your plan here: {link}",
    (FailureClass.UNKNOWN, "email"):
        "Hello {name}, your recent payment of Rs {amt} could not be completed. "
        "You can safely complete it here: {link}",
    # --- Paytm-specific templates ---
    (FailureClass.WALLET_INSUFFICIENT, "whatsapp"):
        "Hi {name}, aapka Paytm wallet mein balance kam hai Rs {amt} ke liye. "
        "Wallet mein paisa daal ke yahan pay karein: {link}",
    (FailureClass.WALLET_INSUFFICIENT, "sms"):
        "{name}, Paytm wallet balance low hai. Topup karein aur pay karein: {link}",
    (FailureClass.KYC_INCOMPLETE, "whatsapp"):
        "Hi {name}, aapki Paytm KYC complete nahi hai. Payment ke liye pehle "
        "KYC karva lo bhai — ek dum bahut simple hai: {link}",
    (FailureClass.KYC_INCOMPLETE, "sms"):
        "{name}, Paytm KYC pending hai. Payment ke liye pehle KYC complete karein: {link}",
    (FailureClass.UPI_LIMIT_EXCEEDED, "whatsapp"):
        "Hi {name}, aapka UPI limit ho gaya hai Rs 1 lakh daily ka. "
        "Kal fir se try karein ya wallet/card se pay karein: {link}",
    (FailureClass.UPI_LIMIT_EXCEEDED, "sms"):
        "{name}, UPI daily limit hit ho gaya. Kal retry karein: {link}",
    (FailureClass.MANDATE_LAPSED, "whatsapp"):
        "Hi {name}, aapka Paytm auto-pay mandate expire ho gaya hai. "
        "Service continue ke liye mandate re-authorize karein: {link}",
    (FailureClass.OFFLINE_PAYMENT_PENDING, "whatsapp"):
        "Hi {name}, aapka store payment pending hai. Najdiki Paytm store par "
        "jaakar payment complete karein: {link}",
    (FailureClass.OFFLINE_PAYMENT_PENDING, "sms"):
        "{name}, store payment pending hai. Najdiki store par jaakar pay karein: {link}",
    (FailureClass.TELECOM_NETWORK, "whatsapp"):
        "Hi {name}, network issue ki wajah se Rs {amt} payment fail hua. "
        "Zara phone restart karein aur fir se try karein: {link}",
    (FailureClass.TELECOM_NETWORK, "sms"):
        "{name}, network issue se payment fail hua. Restart phone aur retry karein: {link}",
    (FailureClass.GATEWAY_LATENCY, "whatsapp"):
        "Hi {name}, Paytm gateway slow hone ki wajah se payment incomplete raha. "
        "Ek dum simple hai — dobara try karein: {link}",
    (FailureClass.GATEWAY_LATENCY, "sms"):
        "{name}, gateway slow tha. Retry karein: {link}",
    (FailureClass.DUPLICATE_TRANSACTION, "whatsapp"):
        "Hi {name}, humne duplicate transaction detect kiya. "
        "Paisa vapis aa jayega — koi action ki zaroorat nahi.",
    (FailureClass.DUPLICATE_TRANSACTION, "sms"):
        "{name}, duplicate transaction detected. Paisa vapis aa jayega.",
}

_FALLBACK_ORDER = ["whatsapp", "sms", "email"]


def render_installment(case: RecoveryCase, link_url: str, due: dict,
                       offer: bool = False) -> str:
    """Smart Payment Plan message: the split schedule + the due slice's link.

    Offer copy lays out all installments; collection copy is a short reminder
    for the one due now. Both carry the STOP footer (compliance).
    """
    slices = " + ".join(
        f"Rs {i['amount'] / 100:,.0f}" for i in case.installment_plan
    )
    if offer:
        body = (
            f"Namaste {case.customer.name or 'ji'}! Rs {case.amount / 100:,.0f} "
            f"ek saath bada lag raha hai? Koi baat nahi — aap {slices} me "
            f"pay kar sakte hain (aaj, 15 din, 30 din). Shuru karein pehle "
            f"kiste se: {link_url}"
        )
    else:
        body = (
            f"Hi {case.customer.name or 'ji'}, aapki agli kista "
            f"(Rs {due['amount'] / 100:,.0f}) due hai. Pay karein: {link_url}"
        )
    return body + FOOTER


def render(case: RecoveryCase, action_type: ActionType, channel: str, link: str) -> str:
    cls = case.failure_class
    template = _TEMPLATES.get((cls, channel))
    if template is None:
        for ch in _FALLBACK_ORDER:
            template = _TEMPLATES.get((cls, ch))
            if template:
                break
    template = template or (
        "Hi {name}, please complete your pending payment of Rs {amt}: {link}"
    )
    text = template.format(
        name=case.customer.name or "there",
        amt=f"{case.amount / 100:,.0f}",
        link=link,
        days=case.loss_age_days,
    )
    return text + FOOTER


def llm_polish(text: str, case: RecoveryCase) -> str:
    out = chat_json(
        "Rewrite this Indian payment-recovery SMS/WhatsApp message. Keep meaning, "
        "amount, link, and the opt-out line unchanged. Warm, brief, Hinglish. "
        'JSON: {"text": "..."}',
        text,
    )
    polished = (out or {}).get("text")
    return polished if isinstance(polished, str) and len(polished) > 30 else text


_VOICE_SCRIPTS: dict[FailureClass, str] = {
    FailureClass.INVOICE_OVERDUE: (
        "Namaste {name} ji, main Apex Enterprises ki accounts team se bol rahi hoon. "
        "Sir, aapka ₹{amt} ka invoice {days} din se pending hai. Agar aap convenient "
        "hain to main abhi payment link SMS kar deti hoon, us par ek click me clear "
        "ho jayega. Koi issue ho to aap mujhe wapas call kar sakte hain. Dhanyavaad."
    ),
}


def render_voice_script(case: RecoveryCase, link_url: str) -> tuple[str, str]:
    """Returns (tts_script, sms_followthrough_text). The link always travels by
    SMS — nobody can click a link on a phone call."""
    template = _VOICE_SCRIPTS.get(
        case.failure_class,
        "Namaste {name} ji, ₹{amt} ka payment pending hai. Link SMS me bhej rahe "
        "hain, ek click me complete ho jayega. Dhanyavaad.",
    )
    script = template.format(
        name=case.customer.name or "ji",
        amt=f"{case.amount / 100:,.0f}",
        days=case.loss_age_days,
    )
    # voice calls are high-intrusion: compliance footer is spoken too
    script += " Is message ko band karne ke liye STOP SMS karein."
    sms = render(case, ActionType.NUDGE_SMS, "sms", link_url)
    return script, sms
