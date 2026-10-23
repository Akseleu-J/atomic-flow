"""Frozen v2 fixture propositions, not a general semantic-entailment grader.

Only finite reviewed EN/RU answer forms receive automatic success. Patterns are
diagnostics; other wording requires independent review and cannot auto-pass.
"""
from __future__ import annotations

import re

RUBRIC_VERSION = "fixture-calibrated-answers-v2"
_ABSTAIN = re.compile(
    r"not (?:shown|present|defined|found|visible|included|implemented|provided|specified|available|documented)|"
    r"no (?:information|evidence|code|implementation|data)|cannot (?:determine|find|tell)|can't (?:determine|find|tell)|"
    r"does(?:n't| not) (?:show|contain|define|implement|specify)|"
    r"нет (?:информации|данных|кода|реализации|сведений)|не (?:найден\w*|показан\w*|определ\w*|реализован\w*|указан\w*)|"
    r"невозможно (?:определить|установить)", re.I)

# Each list entry is a required proposition, not merely a required topic word.
_SUBTOTAL = [r"(?:sum|add|сумм\w*|складыв\w*).*(?:price|цен\w*).*(?:times|multipl\w*|\*|умнож\w*|произвед\w*).*(?:quantit\w*|количеств\w*)|"
             r"(?:sum|сумм\w*).*(?:product\w*|произвед\w*).*(?:price|цен\w*).*(?:quantit\w*|количеств\w*)"]
_DISCOUNT = [r"(?:subtotal|сумм\w*|итог\w*).*(?:1\s*-\s*discount|100\s*-|discount.*(?:percent|%|/\s*100)|скидк\w*.*(?:процент|%))|"
             r"(?:reduc\w*|decreas\w*|уменьш\w*).*(?:subtotal|сумм\w*|итог\w*).*(?:discount|скидк\w*)",
             r"(?:round\w*|quantiz\w*|округл\w*).*(?:cent\w*|0\.01|two decimal|2 decimal|копе\w*|двух|двум|2 знак)"]
_REFUND = [r"(?:status|статус\w*).*(?:paid|оплачен\w*)|(?:paid|оплачен\w*).*(?:order\w*|заказ\w*)",
           r"(?:0\s*(?:<=|≤)|non.?negative|неотрицательн\w*|от\s*0).*(?:30)|(?:within|up to|at most|не более|до)\s*30\s*(?:day\w*|дн\w*)"]
_SHIPPING = [r"(?:50\s*(?:or more|and above|\+|или больше)|(?:>=|≥)\s*50|(?:at least|от|не менее)\s*50).*(?:free|бесплат\w*|0)|"
             r"(?:free|бесплат\w*).*(?:50\s*(?:or more|and above|\+|или больше)|(?:>=|≥)\s*50|(?:at least|от|не менее)\s*50)"]
_CHECKOUT = [r"(?:net|invoice|сч[её]т\w*).*(?:\*\s*\(?\s*1\s*\+\s*(?:vat|tax)|plus.*vat|плюс.*ндс)|"
             r"(?:vat|ндс).*(?:add|прибав\w*|добав\w*)",
             r"(?:shipping|доставк\w*).*(?:net|invoice|сч[её]т\w*|before.*(?:vat|tax)|до.*ндс)|"
             r"(?:net|invoice|сч[её]т\w*).*(?:shipping|доставк\w*)"]

RUBRICS = {
    "invoice-subtotal": (_SUBTOTAL, [r"subtract|вычит\w*|делит\w*|divid\w*"]),
    "discount-rounding": (_DISCOUNT, [r"(?:increase|увелич\w*).*(?:discount|скидк\w*)"]),
    "russian-discount": (_DISCOUNT, [r"(?:increase|увелич\w*).*(?:discount|скидк\w*)"]),
    "ru-invoice-total": (_DISCOUNT, [r"(?:increase|увелич\w*).*(?:discount|скидк\w*)"]),
    "refund-window": (_REFUND, [r"(?:unpaid|неоплачен\w*).*(?:eligible|allowed|разреш\w*)", r"(?:after|более)\s*30"]),
    "refund-call-chain": ([r"is_refundable", r"(?:true|истин\w*).*(?:approved)|approved.*(?:true|истин\w*)",
                           r"(?:false|лож\w*).*(?:denied)|denied.*(?:false|лож\w*)"],
                          [r"(?:true|истин\w*)[^.;\n]{0,40}denied", r"(?:false|лож\w*)[^.;\n]{0,40}approved"]),
    "multi-refund-decision": ([r"is_refundable", r"(?:false|лож\w*).*(?:denied)|denied.*(?:false|лож\w*)"],
                              [r"(?:true|истин\w*)[^.;\n]{0,40}denied"]),
    "ru-shipping": (_SHIPPING, [r"(?:below|less than|under|меньше|ниже)\s*50.*(?:free|бесплат\w*)"]),
    "free-shipping": (_SHIPPING + [r"(?:express|экспресс).*(?:9\.98|doubl\w*|twice|удва\w*|вдвое)|"
                                  r"(?:9\.98|doubl\w*|twice|удва\w*|вдвое).*(?:express|экспресс)"],
                      [r"(?:below|less than|under|меньше|ниже)\s*50.*(?:free|бесплат\w*)"]),
    "vat-unknown-country": ([r"(?:unknown|missing|absent|not in|неизвестн\w*|отсутств\w*).*(?:0(?:\.0)?|zero|нул\w*)|"
                             r"(?:0(?:\.0)?|zero|нул\w*).*(?:unknown|missing|absent|not in|неизвестн\w*|отсутств\w*)"], []),
    "checkout-composition": (_CHECKOUT, [r"shipping[^.;\n]{0,30}(?:multipl\w* by|\*)[^.;\n]{0,15}vat"]),
    "multi-checkout-flow": (_CHECKOUT, [r"shipping[^.;\n]{0,30}(?:multipl\w* by|\*)[^.;\n]{0,15}vat"]),
}


def _normalize(text):
    return " ".join(text.casefold().replace("`", "").split()).rstrip(".! ")


# Only these reviewed complete answer forms can receive automatic success.
# Novel paraphrases and appended claims need independent review; proposition
# regexes below are diagnostics, not a semantic truth oracle.
_ANSWERS = {
    "invoice-subtotal": ["Sum price times quantity", "The subtotal is the sum of price times quantity for all invoice items.",
                         "Промежуточная сумма равна сумме произведений цены на количество всех позиций."],
    "discount-rounding": ["The subtotal is multiplied by (100 - discount_percent) / 100 and quantized to Decimal('0.01') using the current Decimal context.",
                          "Промежуточная сумма умножается на (100 - discount_percent) / 100 и округляется через quantize(Decimal('0.01')) с текущим контекстом Decimal."],
    "refund-window": ["An order is refundable only if its status is paid and 0 <= days_since_purchase <= 30.",
                      "Возврат разрешён только при статусе paid и 0 <= days_since_purchase <= 30."],
    "refund-call-chain": ["refund_message calls is_refundable: True returns refund approved; False returns refund denied."],
    "multi-refund-decision": ["refund_message returns refund denied when is_refundable is False because the order is not paid or days_since_purchase is outside 0 to 30 inclusive."],
    "free-shipping": ["Shipping is free at subtotal >= 50; otherwise standard shipping costs 4.99 and express doubles it to 9.98."],
    "ru-shipping": ["Доставка бесплатна при сумме от 50 включительно.", "Shipping is free for orders of at least 50."],
    "vat-unknown-country": ["An unknown country receives a VAT rate of 0.", "Для неизвестной страны ставка НДС равна 0."],
    "checkout-composition": ["checkout_total applies VAT to Invoice.total(), adds shipping calculated on the net amount, and rounds the result to two decimal places."],
}
for _alias, _original in {"russian-discount": "discount-rounding", "ru-invoice-total": "discount-rounding",
                          "multi-checkout-flow": "checkout-composition"}.items():
    _ANSWERS[_alias] = _ANSWERS[_original]
CALIBRATED_ANSWERS = {key: frozenset(map(_normalize, values)) for key, values in _ANSWERS.items()}
CALIBRATED_ABSTENTIONS = frozenset(map(_normalize, [
    "The supplied source does not provide enough information to answer this question.",
    "No information about bank payouts is shown in the supplied source.",
    "В предоставленном исходном коде нет данных для ответа на этот вопрос.",
]))


def grade_propositions(case: dict, text: str) -> dict:
    normalized = " ".join(text.lower().split())
    if case.get("must_abstain"):
        # A low-confidence assertion is not an abstention; missing_context is advisory.
        # Conservatively require every sentence to express missing evidence. This
        # rejects an abstention followed by an unsupported answer.
        sentences = [s for s in re.split(r"[.!?;\n]+", text) if s.strip()]
        explicit = bool(sentences) and all(_ABSTAIN.search(s) for s in sentences)
        calibrated = _normalize(text) in CALIBRATED_ABSTENTIONS
        return {"semantic_status": "matched" if calibrated else "needs_review" if explicit else "failed",
                "semantic_checks": {"explicit_abstention_only": explicit, "calibrated_answer": calibrated},
                "abstained": calibrated if calibrated or not explicit else None}
    rubric = RUBRICS.get(case["id"])
    if rubric is None:
        return {"semantic_status": "needs_review", "semantic_checks": {}, "abstained": None}
    requirements, contradictions = rubric
    checks = {f"proposition_{i + 1}": bool(re.search(pattern, normalized, re.I)) for i, pattern in enumerate(requirements)}
    checks.update({f"contradiction_{i + 1}_absent": not bool(re.search(pattern, normalized, re.I))
                   for i, pattern in enumerate(contradictions)})
    candidate = all(checks.values())
    calibrated = _normalize(text) in CALIBRATED_ANSWERS.get(case["id"], ())
    checks["calibrated_answer"] = calibrated
    return {"semantic_status": "matched" if calibrated else "needs_review" if candidate else "failed",
            "semantic_checks": checks, "abstained": None}
