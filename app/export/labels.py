"""Fixed wording on a printed paper (headings, "Time Allowed", marking-scheme table) in the paper's language.

English is the reference text. The Indian-language tables below use the conventional exam-paper phrasing for each
language; a school with its own house style should have a teacher in that language read them once. A language with no
table here (Nepali, Sanskrit, Assamese) prints these labels in English.
"""
from __future__ import annotations

from typing import Dict, Optional

from app import languages

_EN: Dict[str, str] = {
    "time_allowed": "Time Allowed", "max_marks": "Maximum Marks", "name": "Name", "roll_no": "Roll No.",
    "date": "Date", "class_level": "Class/Level", "general_instructions": "GENERAL INSTRUCTIONS",
    "section": "SECTION", "answer_key": "ANSWER KEY & MARKING SCHEME",
    "teachers_copy": "TEACHER'S COPY — QUESTIONS WITH ANSWERS", "answer": "ANSWER", "marking_scheme": "Marking scheme",
    "marks": "Marks", "total": "Total", "column_i": "Column I", "column_ii": "Column II", "or": "OR",
    "objective_key": "OBJECTIVE ANSWER KEY", "correct_option": "Correct option", "page": "Page {p} of {t}",
    "question_paper": "Question paper", "answer_key_short": "Answer key", "teachers_copy_short": "Teacher's copy",
    "set": "SET", "set_short": "Set", "attempt": "Attempt any {a} of {n} questions", "marks_word": "marks",
    "negative": "−{x} for each incorrect answer", "hours": "{h} hours", "hour": "{h} hour", "minutes": "{m} minutes",
}

_T: Dict[str, Dict[str, str]] = {
    "hi": {
        "time_allowed": "समय", "max_marks": "पूर्णांक", "name": "नाम", "roll_no": "अनुक्रमांक", "date": "दिनांक",
        "class_level": "कक्षा/स्तर", "general_instructions": "सामान्य निर्देश", "section": "खंड",
        "answer_key": "उत्तर कुंजी एवं अंकन योजना", "teachers_copy": "शिक्षक प्रति — प्रश्न एवं उत्तर",
        "answer": "उत्तर", "marking_scheme": "अंकन योजना", "marks": "अंक", "total": "कुल", "column_i": "स्तंभ I",
        "column_ii": "स्तंभ II", "or": "अथवा", "objective_key": "वस्तुनिष्ठ उत्तर कुंजी", "correct_option": "सही विकल्प",
        "page": "पृष्ठ {p} / {t}", "question_paper": "प्रश्न पत्र", "answer_key_short": "उत्तर कुंजी",
        "teachers_copy_short": "शिक्षक प्रति", "set": "सेट", "set_short": "सेट",
        "attempt": "{n} में से किन्हीं {a} प्रश्नों के उत्तर दीजिए", "marks_word": "अंक",
        "negative": "प्रत्येक गलत उत्तर पर −{x}", "hours": "{h} घंटे", "hour": "{h} घंटा", "minutes": "{m} मिनट",
    },
    "mr": {
        "time_allowed": "वेळ", "max_marks": "पूर्ण गुण", "name": "नाव", "roll_no": "क्रमांक", "date": "दिनांक",
        "class_level": "इयत्ता/स्तर", "general_instructions": "सर्वसाधारण सूचना", "section": "विभाग",
        "answer_key": "उत्तरसूची व गुणदान योजना", "teachers_copy": "शिक्षक प्रत — प्रश्न व उत्तरे",
        "answer": "उत्तर", "marking_scheme": "गुणदान योजना", "marks": "गुण", "total": "एकूण", "column_i": "स्तंभ I",
        "column_ii": "स्तंभ II", "or": "किंवा", "objective_key": "वस्तुनिष्ठ उत्तरसूची", "correct_option": "योग्य पर्याय",
        "page": "पृष्ठ {p} / {t}", "question_paper": "प्रश्नपत्रिका", "answer_key_short": "उत्तरसूची",
        "teachers_copy_short": "शिक्षक प्रत", "set": "संच", "set_short": "संच",
        "attempt": "{n} पैकी कोणतेही {a} प्रश्न सोडवा", "marks_word": "गुण",
        "negative": "प्रत्येक चुकीच्या उत्तराला −{x}", "hours": "{h} तास", "hour": "{h} तास", "minutes": "{m} मिनिटे",
    },
    "bn": {
        "time_allowed": "সময়", "max_marks": "পূর্ণমান", "name": "নাম", "roll_no": "রোল নং", "date": "তারিখ",
        "class_level": "শ্রেণি/স্তর", "general_instructions": "সাধারণ নির্দেশাবলি", "section": "বিভাগ",
        "answer_key": "উত্তরমালা ও নম্বর বিভাজন", "teachers_copy": "শিক্ষকের কপি — প্রশ্ন ও উত্তর",
        "answer": "উত্তর", "marking_scheme": "নম্বর বিভাজন", "marks": "নম্বর", "total": "মোট", "column_i": "স্তম্ভ I",
        "column_ii": "স্তম্ভ II", "or": "অথবা", "objective_key": "বহুনির্বাচনী উত্তরমালা", "correct_option": "সঠিক বিকল্প",
        "page": "পৃষ্ঠা {p} / {t}", "question_paper": "প্রশ্নপত্র", "answer_key_short": "উত্তরমালা",
        "teachers_copy_short": "শিক্ষকের কপি", "set": "সেট", "set_short": "সেট",
        "attempt": "{n}টি প্রশ্নের মধ্যে যেকোনো {a}টির উত্তর দাও", "marks_word": "নম্বর",
        "negative": "প্রতিটি ভুল উত্তরের জন্য −{x}", "hours": "{h} ঘণ্টা", "hour": "{h} ঘণ্টা", "minutes": "{m} মিনিট",
    },
    "gu": {
        "time_allowed": "સમય", "max_marks": "કુલ ગુણ", "name": "નામ", "roll_no": "ક્રમાંક", "date": "તારીખ",
        "class_level": "ધોરણ/સ્તર", "general_instructions": "સામાન્ય સૂચનાઓ", "section": "વિભાગ",
        "answer_key": "ઉત્તરસૂચિ અને ગુણાંકન યોજના", "teachers_copy": "શિક્ષક નકલ — પ્રશ્નો અને ઉત્તરો",
        "answer": "ઉત્તર", "marking_scheme": "ગુણાંકન યોજના", "marks": "ગુણ", "total": "કુલ", "column_i": "સ્તંભ I",
        "column_ii": "સ્તંભ II", "or": "અથવા", "objective_key": "વસ્તુલક્ષી ઉત્તરસૂચિ", "correct_option": "સાચો વિકલ્પ",
        "page": "પૃષ્ઠ {p} / {t}", "question_paper": "પ્રશ્નપત્ર", "answer_key_short": "ઉત્તરસૂચિ",
        "teachers_copy_short": "શિક્ષક નકલ", "set": "સેટ", "set_short": "સેટ",
        "attempt": "{n} માંથી કોઈપણ {a} પ્રશ્નોના ઉત્તર આપો", "marks_word": "ગુણ",
        "negative": "દરેક ખોટા જવાબ માટે −{x}", "hours": "{h} કલાક", "hour": "{h} કલાક", "minutes": "{m} મિનિટ",
    },
    "pa": {
        "time_allowed": "ਸਮਾਂ", "max_marks": "ਕੁੱਲ ਅੰਕ", "name": "ਨਾਂ", "roll_no": "ਰੋਲ ਨੰ.", "date": "ਮਿਤੀ",
        "class_level": "ਜਮਾਤ/ਪੱਧਰ", "general_instructions": "ਆਮ ਹਦਾਇਤਾਂ", "section": "ਭਾਗ",
        "answer_key": "ਉੱਤਰ ਕੁੰਜੀ ਅਤੇ ਅੰਕ ਵੰਡ", "teachers_copy": "ਅਧਿਆਪਕ ਕਾਪੀ — ਪ੍ਰਸ਼ਨ ਅਤੇ ਉੱਤਰ",
        "answer": "ਉੱਤਰ", "marking_scheme": "ਅੰਕ ਵੰਡ", "marks": "ਅੰਕ", "total": "ਕੁੱਲ", "column_i": "ਕਾਲਮ I",
        "column_ii": "ਕਾਲਮ II", "or": "ਜਾਂ", "objective_key": "ਵਸਤੂਨਿਸ਼ਠ ਉੱਤਰ ਕੁੰਜੀ", "correct_option": "ਸਹੀ ਵਿਕਲਪ",
        "page": "ਸਫ਼ਾ {p} / {t}", "question_paper": "ਪ੍ਰਸ਼ਨ ਪੱਤਰ", "answer_key_short": "ਉੱਤਰ ਕੁੰਜੀ",
        "teachers_copy_short": "ਅਧਿਆਪਕ ਕਾਪੀ", "set": "ਸੈੱਟ", "set_short": "ਸੈੱਟ",
        "attempt": "{n} ਵਿੱਚੋਂ ਕੋਈ {a} ਪ੍ਰਸ਼ਨ ਹੱਲ ਕਰੋ", "marks_word": "ਅੰਕ",
        "negative": "ਹਰੇਕ ਗਲਤ ਉੱਤਰ ਲਈ −{x}", "hours": "{h} ਘੰਟੇ", "hour": "{h} ਘੰਟਾ", "minutes": "{m} ਮਿੰਟ",
    },
    "or": {
        "time_allowed": "ସମୟ", "max_marks": "ପୂର୍ଣ୍ଣମାନ", "name": "ନାମ", "roll_no": "ରୋଲ୍ ନମ୍ବର", "date": "ତାରିଖ",
        "class_level": "ଶ୍ରେଣୀ/ସ୍ତର", "general_instructions": "ସାଧାରଣ ନିର୍ଦ୍ଦେଶାବଳୀ", "section": "ବିଭାଗ",
        "answer_key": "ଉତ୍ତରମାଳା ଓ ନମ୍ବର ବିଭାଜନ", "teachers_copy": "ଶିକ୍ଷକ ପ୍ରତି — ପ୍ରଶ୍ନ ଓ ଉତ୍ତର",
        "answer": "ଉତ୍ତର", "marking_scheme": "ନମ୍ବର ବିଭାଜନ", "marks": "ନମ୍ବର", "total": "ମୋଟ", "column_i": "ସ୍ତମ୍ଭ I",
        "column_ii": "ସ୍ତମ୍ଭ II", "or": "ଅଥବା", "objective_key": "ବସ୍ତୁନିଷ୍ଠ ଉତ୍ତରମାଳା", "correct_option": "ଠିକ୍ ବିକଳ୍ପ",
        "page": "ପୃଷ୍ଠା {p} / {t}", "question_paper": "ପ୍ରଶ୍ନପତ୍ର", "answer_key_short": "ଉତ୍ତରମାଳା",
        "teachers_copy_short": "ଶିକ୍ଷକ ପ୍ରତି", "set": "ସେଟ୍", "set_short": "ସେଟ୍",
        "attempt": "{n} ମଧ୍ୟରୁ ଯେକୌଣସି {a}ଟି ପ୍ରଶ୍ନର ଉତ୍ତର ଦିଅ", "marks_word": "ନମ୍ବର",
        "negative": "ପ୍ରତ୍ୟେକ ଭୁଲ ଉତ୍ତର ପାଇଁ −{x}", "hours": "{h} ଘଣ୍ଟା", "hour": "{h} ଘଣ୍ଟା", "minutes": "{m} ମିନିଟ୍",
    },
    "ta": {
        "time_allowed": "நேரம்", "max_marks": "மொத்த மதிப்பெண்கள்", "name": "பெயர்", "roll_no": "பதிவு எண்",
        "date": "நாள்", "class_level": "வகுப்பு/நிலை", "general_instructions": "பொதுவான அறிவுரைகள்", "section": "பிரிவு",
        "answer_key": "விடைக்குறிப்பு மற்றும் மதிப்பெண் திட்டம்", "teachers_copy": "ஆசிரியர் பிரதி — வினாக்களும் விடைகளும்",
        "answer": "விடை", "marking_scheme": "மதிப்பெண் திட்டம்", "marks": "மதிப்பெண்கள்", "total": "மொத்தம்",
        "column_i": "நெடுவரிசை I", "column_ii": "நெடுவரிசை II", "or": "அல்லது", "objective_key": "தெரிவு வினா விடைக்குறிப்பு",
        "correct_option": "சரியான விடை", "page": "பக்கம் {p} / {t}", "question_paper": "வினாத்தாள்",
        "answer_key_short": "விடைக்குறிப்பு", "teachers_copy_short": "ஆசிரியர் பிரதி", "set": "தொகுப்பு",
        "set_short": "தொகுப்பு", "attempt": "{n} வினாக்களில் ஏதேனும் {a} வினாக்களுக்கு விடையளிக்கவும்",
        "marks_word": "மதிப்பெண்கள்", "negative": "ஒவ்வொரு தவறான விடைக்கும் −{x}", "hours": "{h} மணி நேரம்",
        "hour": "{h} மணி நேரம்", "minutes": "{m} நிமிடங்கள்",
    },
    "te": {
        "time_allowed": "సమయం", "max_marks": "గరిష్ఠ మార్కులు", "name": "పేరు", "roll_no": "రోల్ నంబర్",
        "date": "తేదీ", "class_level": "తరగతి/స్థాయి", "general_instructions": "సాధారణ సూచనలు", "section": "విభాగం",
        "answer_key": "జవాబు కీ మరియు మార్కుల పథకం", "teachers_copy": "ఉపాధ్యాయుల ప్రతి — ప్రశ్నలు, సమాధానాలు",
        "answer": "సమాధానం", "marking_scheme": "మార్కుల పథకం", "marks": "మార్కులు", "total": "మొత్తం",
        "column_i": "కాలమ్ I", "column_ii": "కాలమ్ II", "or": "లేదా", "objective_key": "ఆబ్జెక్టివ్ జవాబు కీ",
        "correct_option": "సరైన ఎంపిక", "page": "పేజీ {p} / {t}", "question_paper": "ప్రశ్నపత్రం",
        "answer_key_short": "జవాబు కీ", "teachers_copy_short": "ఉపాధ్యాయ ప్రతి", "set": "సెట్", "set_short": "సెట్",
        "attempt": "{n} ప్రశ్నలలో ఏవైనా {a} ప్రశ్నలకు సమాధానం రాయండి", "marks_word": "మార్కులు",
        "negative": "ప్రతి తప్పు సమాధానానికి −{x}", "hours": "{h} గంటలు", "hour": "{h} గంట", "minutes": "{m} నిమిషాలు",
    },
    "kn": {
        "time_allowed": "ಸಮಯ", "max_marks": "ಗರಿಷ್ಠ ಅಂಕಗಳು", "name": "ಹೆಸರು", "roll_no": "ಕ್ರಮ ಸಂಖ್ಯೆ",
        "date": "ದಿನಾಂಕ", "class_level": "ತರಗತಿ/ಮಟ್ಟ", "general_instructions": "ಸಾಮಾನ್ಯ ಸೂಚನೆಗಳು", "section": "ವಿಭಾಗ",
        "answer_key": "ಉತ್ತರ ಕೀ ಮತ್ತು ಅಂಕ ಯೋಜನೆ", "teachers_copy": "ಶಿಕ್ಷಕರ ಪ್ರತಿ — ಪ್ರಶ್ನೆಗಳು ಮತ್ತು ಉತ್ತರಗಳು",
        "answer": "ಉತ್ತರ", "marking_scheme": "ಅಂಕ ಯೋಜನೆ", "marks": "ಅಂಕಗಳು", "total": "ಒಟ್ಟು", "column_i": "ಕಾಲಂ I",
        "column_ii": "ಕಾಲಂ II", "or": "ಅಥವಾ", "objective_key": "ವಸ್ತುನಿಷ್ಠ ಉತ್ತರ ಕೀ", "correct_option": "ಸರಿಯಾದ ಆಯ್ಕೆ",
        "page": "ಪುಟ {p} / {t}", "question_paper": "ಪ್ರಶ್ನೆ ಪತ್ರಿಕೆ", "answer_key_short": "ಉತ್ತರ ಕೀ",
        "teachers_copy_short": "ಶಿಕ್ಷಕರ ಪ್ರತಿ", "set": "ಸೆಟ್", "set_short": "ಸೆಟ್",
        "attempt": "{n} ಪ್ರಶ್ನೆಗಳಲ್ಲಿ ಯಾವುದಾದರೂ {a} ಪ್ರಶ್ನೆಗಳಿಗೆ ಉತ್ತರಿಸಿ", "marks_word": "ಅಂಕಗಳು",
        "negative": "ಪ್ರತಿ ತಪ್ಪು ಉತ್ತರಕ್ಕೆ −{x}", "hours": "{h} ಗಂಟೆ", "hour": "{h} ಗಂಟೆ", "minutes": "{m} ನಿಮಿಷ",
    },
    "ml": {
        "time_allowed": "സമയം", "max_marks": "പരമാവധി സ്കോർ", "name": "പേര്", "roll_no": "റോൾ നമ്പർ",
        "date": "തീയതി", "class_level": "ക്ലാസ്/നില", "general_instructions": "പൊതു നിർദ്ദേശങ്ങൾ", "section": "വിഭാഗം",
        "answer_key": "ഉത്തരസൂചികയും സ്കോറിങ് രീതിയും", "teachers_copy": "അധ്യാപക പകർപ്പ് — ചോദ്യങ്ങളും ഉത്തരങ്ങളും",
        "answer": "ഉത്തരം", "marking_scheme": "സ്കോറിങ് രീതി", "marks": "സ്കോർ", "total": "ആകെ", "column_i": "കോളം I",
        "column_ii": "കോളം II", "or": "അല്ലെങ്കിൽ", "objective_key": "ഒബ്ജക്ടീവ് ഉത്തരസൂചിക",
        "correct_option": "ശരിയായ ഓപ്ഷൻ", "page": "പേജ് {p} / {t}", "question_paper": "ചോദ്യപേപ്പർ",
        "answer_key_short": "ഉത്തരസൂചിക", "teachers_copy_short": "അധ്യാപക പകർപ്പ്", "set": "സെറ്റ്", "set_short": "സെറ്റ്",
        "attempt": "{n} ചോദ്യങ്ങളിൽ ഏതെങ്കിലും {a} എണ്ണത്തിന് ഉത്തരമെഴുതുക", "marks_word": "സ്കോർ",
        "negative": "ഓരോ തെറ്റായ ഉത്തരത്തിനും −{x}", "hours": "{h} മണിക്കൂർ", "hour": "{h} മണിക്കൂർ", "minutes": "{m} മിനിറ്റ്",
    },
}


class Labels:
    def __init__(self, code: str = "en"):
        self.code = code if code in _T else "en"
        self._t = _T.get(self.code, _EN)

    @property
    def english(self) -> bool:
        return self.code == "en"

    def get(self, key: str, **fmt) -> str:
        text = self._t.get(key) or _EN[key]
        return text.format(**fmt) if fmt else text

    def duration(self, minutes: int) -> str:
        h, m = divmod(int(minutes), 60)
        parts = []
        if h:
            parts.append(self.get("hour" if h == 1 and self.english else "hours", h=h))
        if m:
            parts.append(self.get("minutes", m=m))
        return " ".join(parts) or "—"


ENGLISH = Labels("en")


def for_language(language: Optional[str]) -> Labels:
    lang = languages.resolve(language)
    return Labels(lang.code) if lang else ENGLISH
