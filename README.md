# SADAF CRM — Backend

FastAPI backend. Barcha ma'lumotlar **JSON fayllarda** saqlanadi (`backend/data/`),
hech qanday tashqi baza kerak emas.

---

## 1. O'rnatish

```bash
cd backend

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

## 2. Sozlash

```bash
cp .env.example .env
```

`.env` faylni oching va kamida `JWT_SECRET` ni o'zgartiring:

```bash
# Xavfsiz kalit yaratish
openssl rand -hex 32
```

| O'zgaruvchi | Nima uchun | Majburiymi |
|---|---|---|
| `JWT_SECRET` | Token imzolash kaliti | **Ha** (productionda) |
| `JWT_EXPIRE_MINUTES` | Token amal muddati (standart 720 = 12 soat) | Yo'q |
| `ADMIN_LOGIN` / `ADMIN_PASSWORD` | Birinchi Super Admin | Yo'q |
| `CORS_ORIGINS` | Frontend manzillari, vergul bilan | Yo'q |
| `GROQ_API_KEY` | AI yordamchi (Groq) kaliti — <https://console.groq.com/keys> | Yo'q — bo'sh bo'lsa oflayn rejim |
| `GROQ_MODEL` | AI modeli (standart `openai/gpt-oss-120b`, Free Tier) | Yo'q |
| `GROQ_FALLBACK_MODEL` | Zaxira model, limit/xatoda (standart `openai/gpt-oss-20b`; bo'sh = o'chiq) | Yo'q |
| `GROQ_REASONING_EFFORT`, `AI_MAX_TOKENS`, `AI_TEMPERATURE`, `GROQ_TIMEOUT_SECONDS` | Model sozlamalari | Yo'q |
| `SEED_LEGACY_MENEJER` | `menejer`/`12345` Super Admin hisobini avtomatik yaratish (eski moslik) | Yo'q — productionda `false` |
| `APP_DEBUG` | 500-xato tafsilotini API javobida ko'rsatish | Yo'q — productionda `false` |
| `DATA_DIR` | Ma'lumotlar papkasi (standart `backend/data`) | Yo'q |
| `GROQ_STT_MODEL` | AI Call Center speech-to-text (standart `whisper-large-v3-turbo`) | Yo'q |
| `GROQ_STT_LANGUAGE` | Suhbat tili (standart `uz`; bo'sh = avto) | Yo'q |
| `GROQ_STT_MAX_MB` | STT fayl limiti (Free Tier 25 MB) | Yo'q |
| `GROQ_CALL_MAX_TOKENS`, `GROQ_CALL_TRANSCRIPT_CHARS` | Qo'ng'iroq analizi token/transcript limitlari | Yo'q |
| `XAI_API_KEY`, `XAI_MODEL`, ... | Eski moslik: faqat `GROQ_API_KEY` bo'sh bo'lsa Call Center xAI'da ishlaydi | Yo'q — **majburiy emas** |
| `RECORDINGS_DIR`, `CALL_MAX_UPLOAD_MB` | Audio papkasi (standart `data/recordings`) va yuklash limiti | Yo'q |

## 3. Ishga tushirish

```bash
uvicorn main:app --reload --port 8000
```

Birinchi ishga tushishda `data/` papkada JSON fayllar va Super Admin yaratiladi:

```
login:    ADMIN_LOGIN qiymati   (standart: admin)
password: ADMIN_PASSWORD qiymati (standart: admin123)
role:     super_admin
```

`SEED_LEGACY_MENEJER=true` (standart, eski moslik uchun) bo'lsa qo'shimcha
`menejer` / `12345` Super Admin hisobi ham yaratiladi. Productionda uni
`false` qiling yoki parolini darhol o'zgartiring — standart parolda qolgan
Super Admin bo'lsa backend ishga tushishda ogohlantiradi.

> **Muhim:** birinchi kirishdan keyin *Sozlamalar → Login va parol* orqali
> parolni albatta o'zgartiring.

Interaktiv API hujjati: <http://localhost:8000/docs>

---

## Ma'lumot fayllari

```
backend/data/
  users.json          hodimlar + hisob ma'lumotlari (parol PBKDF2 hash)
  leads.json          ledlar
  clients.json        mijozlar
  tours.json          tur paketlari
  sales.json          yopilgan savdolar (leaddan avtomatik)
  tasks.json          vazifalar
  notifications.json  bildirishnomalar
  attendance.json     Check in / Check out
  settings.json       har bir account uchun fon va ko'rinish
  ai_chats.json       AI suhbat tarixi
  activity.json       faoliyat jurnali
  calls.json          AI Call Center: har bir qo'ng'iroq alohida yozuv
  call_transcripts.json  qo'ng'iroq transcriptlari (callId bo'yicha 1:1)
  call_analyses.json  AI qo'ng'iroq analizlari (callId bo'yicha 1:1)
  recordings/         qo'ng'iroq audiolari (uuid nomli WAV; .gitignore da)
```

Yozish **atomar**: avval `.tmp` faylga yoziladi, keyin `os.replace` bilan
almashtiriladi. Har bir fayl uchun alohida lock — parallel so'rovlar
ma'lumotni buzmaydi.

---

## Rollar va huquqlar

| Rol | Ko'radi | Sahifalar |
|---|---|---|
| `super_admin` | Hammasini | Barchasi |
| `admin` | Hammasini | Barchasi (Super Admin hisobiga tegmaydi) |
| `manager` | O'z jamoasini | Barchasi |
| `operator` | Faqat o'ziga biriktirilganni | Dashboard, Ledlar, Mijozlar, Vazifalar, Bildirishnomalar, AI, Sozlamalar |

Menejer jamoasi — `managerId` maydoni orqali biriktiriladi.

Huquqlar **ikki joyda** tekshiriladi: frontend navbarda (qulaylik uchun) va
backend har bir endpointda (xavfsizlik uchun). Frontendni chetlab o'tib
API'ga to'g'ridan-to'g'ri murojaat qilish ish bermaydi.

---

## API

| Prefiks | Vazifasi |
|---|---|
| `/api/auth` | login, me, profil, parol |
| `/api/employees` | hodim hisoblari, `/directory`, `/roles` |
| `/api/leads` | ledlar, `PATCH /{id}/stage` |
| `/api/clients` | mijozlar |
| `/api/tours` | tur paketlari |
| `/api/sales` | savdolar |
| `/api/tasks` | vazifalar, `PATCH /{id}/toggle` |
| `/api/notifications` | bildirishnomalar, `/unread-count` |
| `/api/attendance` | `/check-in`, `/check-out`, `/today`, `/summary` |
| `/api/dashboard` | rolga qarab hisoblangan statistika |
| `/api/analytics` | voronka, menejerlar, yo'nalishlar |
| `/api/reports` | `/excel` |
| `/api/settings` | har bir account uchun fon/ko'rinish |
| `/api/ai` | `/chat`, `/status`, `/history` (Groq) |
| `/api/calls` | AI Call Center (Groq): qo'ng'iroq, `/{id}/recording`, `/{id}/transcribe`, `/{id}/analyze`, `/stats` |
| `/api/leads/{id}/calls` | leadning qo'ng'iroqlar arxivi |
| `/api/integrations/sheets` | `POST /lead` — Google Sheets'dan yangi lead qabul qilish |
| `WS /ws?token=` | real vaqtli sinxronizatsiya (barcha kolleksiyalar) |

Barcha endpointlar (login'dan tashqari) `Authorization: Bearer <token>` talab qiladi.
`/api/integrations/sheets/lead` alohida — u `X-Sheets-Secret` header bilan
himoyalangan (pastga qarang), JWT talab qilmaydi.

---

## Google Sheets integratsiyasi

Google Sheets'dagi (masalan Facebook/Instagram Lead Ads) leadlar **Sheet1** va
**Sheet3** varaqlaridan avtomatik CRM'ga tushadi. Jadval **Google Sheets API
(v4)** orqali o'qiladi (Apps Script Advanced Service) — pullik xizmat yoki
alohida server kerak emas.

**Oqim:** Sheet1 + Sheet3 → Apps Script (har 5 daqiqada, `values.batchGet`) →
`POST /api/integrations/sheets/lead` → CRM'da yangi lead.
Yo'nalish bir tomonlama — CRM Sheetsga hech narsa yozmaydi.

### Sozlash

1. `.env` faylda `SHEETS_WEBHOOK_SECRET` ni o'rnating (`openssl rand -hex 24`).
2. Google Sheets'da: **Extensions → Apps Script** → `backend/scripts/google-sheets-webhook.gs`
   kodini joylashtiring.
3. Apps Script'da **Services (+) → Google Sheets API → Add**.
4. Skriptdagi `CRM_URL` va `SHEETS_SECRET` ni to'ldiring.
5. `checkSetup` ni ishga tushiring — Logs'da har bir varaqning qaysi ustuni
   qaysi CRM maydoniga mos kelgani ko'rinadi (CRM'ga hech narsa yuborilmaydi).
6. `setupTrigger` ni bir marta ishga tushiring — `syncNewLeads` har 5 daqiqada ishlaydi.

### Maydonlar

Ustunlar **sarlavha nomi** bo'yicha topiladi (tartibi muhim emas, Sheet1 va
Sheet3 da har xil bo'lishi mumkin). Jadvalda bo'lmagan ma'lumot CRM'da
**bo'sh** qoladi — hech narsa taxmin qilinmaydi.

| CRM | Jadval ustuni |
|---|---|
| Ism Familiya | `ismingiz` |
| Telefon raqam | `phone_number`, bo'lmasa `telefon_raqamingiz` |
| Qaysi tur | `qaysi_tur` / `tur` — mavjud bo'lsa |
| Nechta odam | `nechta_odam` / `odam_soni` — mavjud bo'lsa |
| Summa (USD) | `summa_usd` / `summa` — mavjud bo'lsa |
| Mas'ul menejer | `masul_menejer` / `menejer` — mavjud bo'lsa |
| Manba | `platform` (`ig` → Instagram, `fb` → Facebook) |
| Bosqich | `lead_status` — CRM bosqichiga mos kelmasa (masalan `CREATED`) → "Yangi" |
| Telegram username | `telegram_username` / `telegram` — mavjud bo'lsa |
| Shahar | `shahar` / `city` — mavjud bo'lsa |
| Kommentariya | `Comment` |
| Sana | `created_time` |

Sarlavhalar normallashtiriladi ("Mas'ul menejer" = `masul_menejer`,
"ismingiz?" = `ismingiz`). Boshqa nomdagi ustun uchun skriptdagi
`HEADER_ALIASES` ga qo'shing. Mas'ul menejer bo'lmagan lead biriktirilmagan
holda qoladi — uni Super Admin ko'radi va CRM'dan biriktiradi.

### Dublikatlar va ishonchlilik

- Dublikat avval `id` ustuni, keyin telefon raqami (formatidan qat'i nazar)
  bo'yicha aniqlanadi — Sheet1 va Sheet3 orasida ham. Bir lead ikki marta qo'shilmaydi.
- Har bir varaq uchun oxirgi yuborilgan qator alohida saqlanadi. CRM javob
  bermasa qator "yuborilgan" deb belgilanmaydi — keyingi ishga tushishda qayta
  yuboriladi (lead yo'qolmaydi).
- Hamma qatorlarni qayta tekshirish uchun `resetSyncState` ni ishga tushiring —
  xavfsiz, dublikatlar qayta qo'shilmaydi.

---

## AI yordamchi (Groq)

AI yordamchi [Groq](https://groq.com) ning OpenAI-mos Chat Completions API'si
orqali ishlaydi (`POST https://api.groq.com/openai/v1/chat/completions`).
Qo'shimcha SDK kerak emas — so'rovlar mavjud `httpx` bilan yuboriladi.

### Sozlash

1. <https://console.groq.com/keys> da bepul kalit oling.
2. `backend/.env` ga yozing:

   ```bash
   GROQ_API_KEY=gsk_...
   GROQ_MODEL=openai/gpt-oss-120b
   GROQ_FALLBACK_MODEL=openai/gpt-oss-20b
   ```

3. Backendni qayta ishga tushiring. Konsolda shunday qator chiqadi:

   ```
   [sadaf] AI yordamchi: Groq (openai/gpt-oss-120b, zaxira: openai/gpt-oss-20b)
   ```

   Tekshirish: `GET /api/health` → `"ai": "groq"`.

### Model tanlovi

| Model | Nima uchun |
|---|---|
| `openai/gpt-oss-120b` (standart) | Free Tier'da mavjud production model, tool calling va ko'p tilli javoblarda eng kuchlisi |
| `openai/gpt-oss-20b` (zaxira) | Tezroq; Groq'da limiti alohida hisoblanadi — asosiy model 429 qaytarsa shunga o'tiladi |

> `llama-3.3-70b-versatile` va `llama-3.1-8b-instant` Groq'da 2026-yil
> 16-avgustdan Free/Developer tarif uchun o'chirilgan — ularni ishlatmang.
> Joriy ro'yxat: <https://console.groq.com/docs/models>.

Free Tier limitlari (taxminan, har bir model uchun alohida): 30 so'rov/daqiqa,
1 000 so'rov/kun, 8 000 token/daqiqa. Shuning uchun tool natijalari ixcham
yuboriladi (kerakli maydonlar, jami soni/summa, max 50 yozuv), suhbat tarixi
oxirgi 8 xabar bilan cheklanadi va `GROQ_REASONING_EFFORT=low` ishlatiladi.
Aniq limitlar: <https://console.groq.com/settings/limits>.

### CRM funksiyalari (tool calling)

Model savolga qarab o'zi kerakli funksiyani chaqiradi:

```
search_leads        search_clients       search_sales        search_tasks
search_tours        get_dashboard_stats  get_sales_analytics get_employee_stats
```

Filtrlar katta-kichik harf va o'zbekcha apostrof variantlariga (`ʻ ʼ ’`)
sezgir emas. Javob standart holatda **o'zbek tilida** (lotin), foydalanuvchi
rus yoki ingliz tilida yozsa — o'sha tilda.

### Ishonchlilik

- 429 / 5xx / timeout / o'chirilgan model → zaxira modelga o'tiladi.
- Model noto'g'ri tool chaqiruvi yaratsa (`tool_use_failed`) → bir marta qayta so'raladi.
- Groq umuman ishlamasa → oflayn (rule-based) javob qaytadi va UI'da qisqa
  ogohlantirish (`notice`) ko'rinadi. CRM hech qachon to'xtamaydi.

### Xavfsizlik

- Funksiyalar **joriy foydalanuvchi doirasida** bajariladi. Model qanday
  so'ramasin, operator o'ziga tegishli bo'lmagan ma'lumotni ololmaydi —
  filtrlash `services/crm.py` ichida, model qaroridan mustaqil.
- `GROQ_API_KEY` faqat `services/groq_service.py` dagi `Authorization`
  headerida ishlatiladi: log qilinmaydi, xato xabariga qo'shilmaydi,
  frontendga yoki bazaga yozilmaydi.
- Frontend yuborgan suhbat tarixida faqat `user`/`assistant` rollari qabul
  qilinadi — `system` ko'rsatmasini kiritib bo'lmaydi.

**Kalit berilmagan bo'lsa** — oflayn (rule-based) rejim ishlaydi. CRM
to'liq ishlayveradi, javoblar soddaroq bo'ladi.

---

## Real vaqtli sinxronizatsiya

Backend `/ws` WebSocket endpointini beradi. Har qanday kolleksiya (leadlar,
mijozlar, vazifalar, hodimlar, savdolar va h.k.) JSON faylga yozilganda —
`app/services/realtime.py` barcha ulangan mijozlarga
`{"type": "collection", "collection": "<nom>"}` signalini yuboradi.
Masalan bir hodim ikkinchisiga vazifa bersa, qabul qiluvchi tomonda
vazifa sahifani yangilamasdan darhol paydo bo'ladi. Ulanish `Authorization`
token bilan emas, `?token=` query-parametri bilan autentifikatsiya qilinadi
(brauzer WebSocket'da maxsus header qo'sha olmaydi).

---

## Zaxira nusxa

`data/` papkani nusxalash kifoya:

```bash
tar czf sadaf-backup-$(date +%F).tar.gz data/
```

---

## AI Call Center (Groq)

Navbardagi **🤖 AI Call Center** tugmasi katta modal ochadi: *Yangi qo'ng'iroq*,
*Arxiv*, *Statistika* va *AI yordamchi* chat. Lead panelidagi **📞 Call**
tugmasi modalni shu lead tanlangan holda ochadi.

**Oqim:** Lead → (rozilik tasdiqlanadi) → brauzer mikrofonidan yozuv (16 kHz mono WAV)
→ `POST /api/calls/{id}/recording` → `POST /api/calls/{id}/transcribe`
(Groq Whisper `whisper-large-v3-turbo`, `language=uz`) → segmentlar AI bilan
ADMIN/MIJOZ ga ajratiladi (qo'lda almashtirish mumkin) → `POST /api/calls/{id}/analyze`
(`GROQ_MODEL` = `openai/gpt-oss-120b`, JSON-sxema: checklist + muammolar + tavsiyalar) → lead arxivi.

**Sozlash:** CRM AI yordamchi uchun berilgan **o'sha `GROQ_API_KEY`** yetarli —
yangi kalit kerak emas. `.env` da ixtiyoriy: `GROQ_STT_MODEL`, `GROQ_STT_LANGUAGE`.
Kalit faqat backendda, faqat `services/groq_service.py` dagi `Authorization`
headerida ishlatiladi; `/api/calls/config` faqat bor/yo'qligini qaytaradi.
Ishga tushganda konsolda:

```
[sadaf] AI Call Center: Groq — STT=whisper-large-v3-turbo (til: uz), analiz=openai/gpt-oss-120b
```

**Xatoliklar (`/transcribe`, `/analyze`):**

| HTTP | Sabab |
|---|---|
| `503` | `GROQ_API_KEY` sozlanmagan (avval bu holat noto'g'ri `502` qaytarardi) |
| `413` | Audio 25 MB dan katta (brauzer yozuvi ≈ 13 daqiqa). Audio arxivda qoladi |
| `429` | Groq limiti tugadi — biroz kutib, arxivdan qayta ishga tushiring |
| `422` | Audioda nutq aniqlanmadi |
| `502` | Groq serveri xato qaytardi / javob bermadi |

Har qanday xatoda audio va qo'ng'iroq yozuvi saqlanib qoladi, status `failed`
bo'ladi va jarayonni arxivdan qayta ishga tushirish mumkin.

**Free Tier limitlari:** Whisper — 25 MB/fayl, ~20 so'rov/daqiqa, 7 200 audio-soniya/soat;
GPT-OSS — ~8K token/daqiqa. Shu sababli analizga transcriptning boshi va oxiri
(standart 10 000 belgi) yuboriladi, rollarni ajratish esa yengilroq
`GROQ_FALLBACK_MODEL` (`openai/gpt-oss-20b`) da bajariladi.

**Eski moslik:** `GROQ_API_KEY` bo'sh, lekin `XAI_API_KEY` (+ `XAI_MODEL`) berilgan
bo'lsa, Call Center avvalgidek xAI orqali ishlaydi. xAI sozlamalari majburiy emas.

**Ruxsatlar (backendda majburiy):**

| Rol | Ko'radi |
|---|---|
| Bosh menejer (`super_admin`) | Barcha qo'ng'iroqlar, audio, transcript, analiz, menejerlar statistikasi |
| Menejer (`admin`) va boshqalar | O'zi qilgan qo'ng'iroqlar + o'ziga ko'rinadigan leadlarning qo'ng'iroqlari |

Qo'ng'iroq egasi doim token egasi — frontend yuborgan `managerId` e'tiborsiz.
Audio, transcript va analizni faqat qo'ng'iroq egasi yoki Bosh menejer
o'zgartiradi. Audio saqlangan qo'ng'iroqni faqat Bosh menejer o'chiradi.

**Muhim cheklovlar:**

- Brauzer faqat **kompyuter mikrofonini** yozadi — bu telefon liniyasining
  o'zini yozish emas. Mijoz ovozi uchun telefon karnay rejimida bo'lishi kerak.
  VoIP (Asterisk/Twilio va h.k.) uchun `calls.channel` / `calls.externalId`
  maydonlari va `services/calls.py` dagi saqlash funksiyalari tayyor.
- Whisper speaker diarization bermaydi — xodim/mijoz rollari matn mazmuniga
  qarab AI tomonidan aniqlanadi; noto'g'ri bo'lsa transcriptda "rollarni
  almashtirish" tugmasi bor. Suhbat rus tilida bo'lsa `GROQ_STT_LANGUAGE=ru`
  qiling yoki bo'sh qoldiring (avto-aniqlash).
- Mijozni yozib olish haqida ogohlantirish va rozilik olish — qonuniy talab;
  UI rozilik tasdig'isiz yozuvni boshlamaydi (`calls.consent` da saqlanadi).
- Render kabi platformalarda disk vaqtinchalik bo'lishi mumkin — audio
  yo'qolmasligi uchun `RECORDINGS_DIR` ni doimiy (persistent) diskka yo'naltiring.

**Testlar:**

```bash
pip install -r requirements-dev.txt
python -m pytest -q          # vaqtinchalik DATA_DIR da ishlaydi, data/ ga tegmaydi
```
