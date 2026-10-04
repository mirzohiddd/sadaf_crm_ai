/**
 * Google Sheets → SADAF CRM: lead importi (Sheet1 va Sheet3).
 *
 * Ma'lumot Google Sheets API (v4, Advanced Service) orqali o'qiladi —
 * ikkala varaq bitta `values.batchGet` so'rovi bilan olinadi.
 *
 * CRM MAYDONLARI ← JADVAL USTUNLARI (ustunlar SARLAVHA NOMI bo'yicha topiladi,
 * tartibi ahamiyatsiz; jadvalda bo'lmagan maydon CRM'da BO'SH qoladi):
 *
 *   Ism Familiya       ← ismingiz
 *   Telefon raqam      ← phone_number, bo'lmasa telefon_raqamingiz
 *   Qaysi tur          ← tur / qaysi_tur            (mavjud bo'lsa)
 *   Nechta odam        ← nechta_odam / odam_soni    (mavjud bo'lsa)
 *   Summa (USD)        ← summa / summa_usd          (mavjud bo'lsa)
 *   Mas'ul menejer     ← masul_menejer / menejer    (mavjud bo'lsa)
 *   Manba              ← platform
 *   Bosqich            ← lead_status  (CRM bosqichi bo'lmasa — "Yangi")
 *   Telegram username  ← telegram / telegram_username (mavjud bo'lsa)
 *   Shahar             ← shahar / city              (mavjud bo'lsa)
 *   Kommentariya       ← Comment
 *   Sana               ← created_time
 *   (dublikat uchun)   ← id
 *
 * Dublikat: CRM avval `id`, keyin telefon bo'yicha tekshiradi — bir lead
 * ikki marta (hatto Sheet1 va Sheet3 da takrorlansa ham) qo'shilmaydi.
 *
 * MUHIM: skript jadvalga HECH NARSA YOZMAYDI. Qaysi qator yuborilgani
 * skriptning o'z xotirasida (PropertiesService), har bir varaq uchun alohida
 * saqlanadi. CRM javob bermasa (tarmoq/server xatosi) qator "yuborilgan" deb
 * belgilanmaydi — keyingi ishga tushishda qayta yuboriladi (lead yo'qolmaydi).
 *
 * O'RNATISH:
 *  1. Google Sheetsda: Extensions → Apps Script.
 *  2. Shu faylning kodini joylashtiring (mavjud kodni almashtiring).
 *  3. Chap menyuda "Services" (+) → "Google Sheets API" → Add.
 *     (Bu shart — skript `Sheets` xizmati orqali ishlaydi.)
 *  4. CRM_URL va SHEETS_SECRET ni to'ldiring (SHEETS_SECRET backend .env
 *     dagi SHEETS_WEBHOOK_SECRET bilan bir xil bo'lishi shart).
 *  5. `checkSetup` ni bir marta ishga tushiring (Run) — har bir varaqda
 *     qaysi ustun qaysi CRM maydoniga mos kelgani Logs'da ko'rinadi.
 *     Birinchi ishga tushirishda Google ruxsat so'raydi — tasdiqlang.
 *  6. `setupTrigger` ni bir marta ishga tushiring — `syncNewLeads` har
 *     5 daqiqada avtomatik ishlaydi.
 */

const CRM_URL = 'https://sizning-domeningiz.uz/api/integrations/sheets/lead';
const SHEETS_SECRET = 'change-me-sheets-secret'; // backend .env dagi SHEETS_WEBHOOK_SECRET bilan bir xil

// Import qilinadigan varaqlar (pastdagi yorliq nomlari).
const SHEET_NAMES = ['Sheet1', 'Sheet3'];

// Bo'sh qoldirilsa — skript ulangan jadval ishlatiladi.
const SPREADSHEET_ID = '';

// Sarlavha 1-qatorda, ma'lumot 2-qatordan boshlanadi.
const HEADER_ROW = 1;
const DATA_START_ROW = 2;

// CRM maydoni → jadval sarlavhasi variantlari (normallashtirilgan: kichik harf,
// apostrof va "?" siz, bo'shliq → "_"). Sarlavhangiz boshqacha bo'lsa shu yerga qo'shing.
const HEADER_ALIASES = {
  externalId:     ['id', 'lead_id'],
  createdTime:    ['created_time', 'sana'],
  platform:       ['platform', 'manba'],
  name:           ['ismingiz', 'ism_familiya', 'ism_familiyangiz', 'ismingiz_familiyangiz', 'full_name', 'ism'],
  phoneFormatted: ['phone_number'],
  phoneRaw:       ['telefon_raqamingiz', 'telefon_raqam', 'telefon'],
  tour:           ['qaysi_tur', 'tur', 'tour'],
  people:         ['nechta_odam', 'odam_soni', 'odamlar_soni', 'people'],
  amount:         ['summa_usd', 'summa', 'amount'],
  manager:        ['masul_menejer', 'menejer', 'manager'],
  leadStatus:     ['lead_status', 'bosqich'],
  telegram:       ['telegram_username', 'telegram'],
  city:           ['shahar', 'city'],
  comment:        ['comment', 'kommentariya', 'izoh']
};

const LOCK_TIMEOUT_MS = 30 * 1000;
const MAX_RUN_MS = 4.5 * 60 * 1000; // Apps Script 6 daqiqa limitidan oldin to'xtaymiz
const LAST_ROW_PREFIX = 'sadaf_crm_last_synced_row:';

/**
 * Asosiy funksiya — vaqt bo'yicha trigger shuni chaqiradi.
 * Har bir varaqda oxirgi yuborilgan qatordan keyingi qatorlarni CRM'ga jo'natadi.
 */
function syncNewLeads() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(LOCK_TIMEOUT_MS)) {
    Logger.log('Boshqa ishga tushirish davom etyapti, o\'tkazib yuborildi.');
    return;
  }

  try {
    const startedAt = Date.now();
    const props = PropertiesService.getScriptProperties();
    const lastRows = {};
    SHEET_NAMES.forEach((name) => {
      lastRows[name] = Number(props.getProperty(LAST_ROW_PREFIX + name) || (DATA_START_ROW - 1));
    });

    const sheets = readSheets(lastRows);

    for (const sheet of sheets) {
      if (!sheet.map) continue;
      let lastSynced = lastRows[sheet.name];
      let sent = 0;
      let duplicates = 0;
      let skipped = 0;

      for (let i = 0; i < sheet.rows.length; i++) {
        if (Date.now() - startedAt > MAX_RUN_MS) {
          Logger.log('%s: vaqt limiti — qolgan qatorlar keyingi ishga tushishda yuboriladi.', sheet.name);
          break;
        }
        const rowNumber = sheet.firstRow + i;
        const result = sendRow(sheet.name, sheet.map, sheet.rows[i], rowNumber);

        if (result === 'retry') break;        // CRM javob bermadi — shu qatordan keyin qayta urinamiz
        if (result === 'stop') {              // maxfiy kalit noto'g'ri — hech narsa belgilanmaydi
          props.setProperty(LAST_ROW_PREFIX + sheet.name, String(lastSynced));
          return;
        }
        if (result === 'sent') sent++;
        else if (result === 'duplicate') duplicates++;
        else skipped++;
        lastSynced = rowNumber;
      }

      props.setProperty(LAST_ROW_PREFIX + sheet.name, String(lastSynced));
      Logger.log('%s: yangi lead — %s, dublikat — %s, o\'tkazib yuborildi — %s (oxirgi qator: %s).',
        sheet.name, sent, duplicates, skipped, lastSynced);
    }
  } finally {
    lock.releaseLock();
  }
}

/**
 * Google Sheets API orqali har bir varaqning sarlavhasi va yangi qatorlarini o'qiydi.
 * Qaytaradi: [{ name, map, firstRow, rows }]
 */
function readSheets(lastRows) {
  if (typeof Sheets === 'undefined') {
    throw new Error('Google Sheets API yoqilmagan: Apps Script → Services (+) → "Google Sheets API" ni qo\'shing.');
  }
  const spreadsheetId = SPREADSHEET_ID || SpreadsheetApp.getActiveSpreadsheet().getId();
  const existing = Sheets.Spreadsheets.get(spreadsheetId, { fields: 'sheets.properties.title' })
    .sheets.map((s) => s.properties.title);

  const names = SHEET_NAMES.filter((name) => {
    if (existing.indexOf(name) === -1) {
      Logger.log('"%s" varag\'i topilmadi — o\'tkazib yuborildi. Mavjud varaqlar: %s', name, existing.join(', '));
      return false;
    }
    return true;
  });
  if (!names.length) return [];

  const ranges = [];
  names.forEach((name) => {
    const q = quoteSheet(name);
    ranges.push(q + '!' + HEADER_ROW + ':' + HEADER_ROW);
    ranges.push(q + '!A' + Math.max(DATA_START_ROW, (lastRows[name] || 0) + 1) + ':ZZ');
  });

  const response = Sheets.Spreadsheets.Values.batchGet(spreadsheetId, {
    ranges: ranges,
    majorDimension: 'ROWS',
    valueRenderOption: 'UNFORMATTED_VALUE',   // raqamlar — raqam (summa, odam soni, telefon)
    dateTimeRenderOption: 'FORMATTED_STRING'  // sanalar — jadvalda ko'ringanidek matn
  });
  const valueRanges = response.valueRanges || [];

  return names.map((name, idx) => {
    const header = ((valueRanges[idx * 2] || {}).values || [[]])[0] || [];
    const rows = (valueRanges[idx * 2 + 1] || {}).values || [];
    const map = buildColumnMap(header);
    const missing = [];
    if (map.name === undefined) missing.push('ismingiz');
    if (map.phoneFormatted === undefined && map.phoneRaw === undefined) missing.push('phone_number / telefon_raqamingiz');
    if (missing.length) {
      Logger.log('%s: majburiy ustun topilmadi (%s) — varaq o\'tkazib yuborildi. Sarlavhalar: %s',
        name, missing.join(', '), header.join(' | '));
      return { name: name, map: null, firstRow: 0, rows: [] };
    }
    return {
      name: name,
      map: map,
      firstRow: Math.max(DATA_START_ROW, (lastRows[name] || 0) + 1),
      rows: rows
    };
  });
}

/** Sarlavhalar ro'yxatidan CRM maydoni → ustun indeksi (0 dan) xaritasini tuzadi. */
function buildColumnMap(header) {
  const normalized = header.map(normalizeHeader);
  const map = {};
  Object.keys(HEADER_ALIASES).forEach((field) => {
    for (const alias of HEADER_ALIASES[field]) {
      const idx = normalized.indexOf(alias);
      if (idx !== -1) {
        map[field] = idx;
        return;
      }
    }
  });
  return map;
}

/** "Mas'ul menejer" → "masul_menejer", "ismingiz?" → "ismingiz", "Summa (USD)" → "summa_usd". */
function normalizeHeader(value) {
  return String(value === null || value === undefined ? '' : value)
    .toLowerCase()
    .replace(/[\u2018\u2019\u02bb\u02bc'`]/g, '')
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');
}

function quoteSheet(name) {
  return "'" + String(name).replace(/'/g, "''") + "'";
}

/**
 * Bitta qatorni CRM'ga yuboradi.
 * Qaytaradi: 'sent' | 'duplicate' | 'skip' (qator yaroqsiz) | 'retry' (keyin qayta) | 'stop' (kalit xato).
 */
function sendRow(sheetName, map, row, rowNumber) {
  const cell = (field) => (map[field] === undefined ? '' : row[map[field]]);
  const text = (field) => {
    const v = cell(field);
    return v === null || v === undefined ? '' : String(v).trim();
  };
  // Raqamli maydonlar: katakcha bo'sh bo'lsa null (CRM'da bo'sh qoladi).
  const numberOrNull = (field) => {
    const v = cell(field);
    if (v === null || v === undefined || String(v).trim() === '') return null;
    return typeof v === 'number' ? v : String(v).trim();
  };

  const name = text('name');
  const phone = normalizePhone(cell('phoneFormatted'), cell('phoneRaw'));
  if (!name || !phone) return 'skip'; // ism yoki telefon yo'q — lead emas

  const payload = {
    name: name,
    phone: phone,
    tour: text('tour'),
    people: numberOrNull('people'),
    amount: numberOrNull('amount'),
    manager: text('manager'),
    platform: text('platform'),
    leadStatus: text('leadStatus'),
    telegram: text('telegram'),
    city: text('city'),
    comment: text('comment'),
    createdTime: text('createdTime'),
    externalId: text('externalId'),
    rowId: rowNumber,
    sheet: sheetName
  };

  let response;
  try {
    response = UrlFetchApp.fetch(CRM_URL, {
      method: 'post',
      contentType: 'application/json',
      headers: { 'X-Sheets-Secret': SHEETS_SECRET },
      payload: JSON.stringify(payload),
      muteHttpExceptions: true
    });
  } catch (err) {
    Logger.log('%s, %s-qator: CRM\'ga ulanib bo\'lmadi (%s) — keyinroq qayta yuboriladi.', sheetName, rowNumber, err);
    return 'retry';
  }

  const code = response.getResponseCode();
  if (code >= 200 && code < 300) {
    let duplicate = false;
    try { duplicate = JSON.parse(response.getContentText()).duplicate === true; } catch (e) { /* javob JSON emas */ }
    Logger.log('%s, %s-qator (%s): %s', sheetName, rowNumber, name, duplicate ? 'dublikat — qo\'shilmadi' : 'yangi lead');
    return duplicate ? 'duplicate' : 'sent';
  }
  if (code === 401) {
    Logger.log('CRM maxfiy kalitni rad etdi (401). SHEETS_SECRET ni tekshiring — import to\'xtatildi.');
    return 'stop';
  }
  if (code === 400 || code === 422) {
    Logger.log('%s, %s-qator: CRM qatorni qabul qilmadi (%s) — %s', sheetName, rowNumber, code, response.getContentText());
    return 'skip';
  }
  Logger.log('%s, %s-qator: CRM xatosi (%s) — keyinroq qayta yuboriladi.', sheetName, rowNumber, code);
  return 'retry';
}

/** "p:+998901234567" → "+998901234567"; phone_number bo'sh bo'lsa telefon_raqamingiz ishlatiladi. */
function normalizePhone(formatted, raw) {
  const f = String(formatted === null || formatted === undefined ? '' : formatted).trim();
  if (f) return f.replace(/^p:/i, '').trim();
  return String(raw === null || raw === undefined ? '' : raw).trim();
}

/**
 * Bir martalik sozlash: `syncNewLeads` uchun har 5 daqiqada ishga tushadigan
 * trigger yaratadi. Qayta ishga tushirsangiz — eski triggerlar tozalanib,
 * faqat bitta trigger qoladi (dublikat bo'lmaydi).
 */
function setupTrigger() {
  ScriptApp.getProjectTriggers()
    .filter((t) => t.getHandlerFunction() === 'syncNewLeads')
    .forEach((t) => ScriptApp.deleteTrigger(t));

  ScriptApp.newTrigger('syncNewLeads')
    .timeBased()
    .everyMinutes(5)
    .create();

  Logger.log('Trigger o\'rnatildi: syncNewLeads har 5 daqiqada ishga tushadi.');
}

/**
 * Sozlamani tekshirish (CRM'ga hech narsa yubormaydi): har bir varaqda qaysi
 * ustun qaysi CRM maydoniga mos kelgani va nechta yangi qator borligini ko'rsatadi.
 */
function checkSetup() {
  const props = PropertiesService.getScriptProperties();
  const lastRows = {};
  SHEET_NAMES.forEach((name) => {
    lastRows[name] = Number(props.getProperty(LAST_ROW_PREFIX + name) || (DATA_START_ROW - 1));
  });
  readSheets(lastRows).forEach((sheet) => {
    if (!sheet.map) return;
    const found = Object.keys(HEADER_ALIASES)
      .map((field) => field + ' → ' + (sheet.map[field] === undefined ? '(yo\'q — bo\'sh qoladi)' : (sheet.map[field] + 1) + '-ustun'));
    Logger.log('%s:\n  %s\n  Yuborilmagan qatorlar: %s', sheet.name, found.join('\n  '), sheet.rows.length);
  });
}

/**
 * Hamma qatorlarni boshidan qayta tekshirish (masalan Sheet3 keyinroq qo'shilgan
 * bo'lsa). Xavfsiz: CRM dublikatlarni (id/telefon) qayta qo'shmaydi.
 */
function resetSyncState() {
  const props = PropertiesService.getScriptProperties();
  SHEET_NAMES.forEach((name) => props.deleteProperty(LAST_ROW_PREFIX + name));
  props.deleteProperty('sadaf_crm_last_synced_row'); // eski versiya kaliti
  Logger.log('Holat tozalandi — keyingi syncNewLeads barcha qatorlarni tekshiradi.');
}

/** CRM_URL va SHEETS_SECRET to'g'ri sozlanganini tekshirish uchun — qo'lda ishga tushiring. */
function testConnection() {
  const options = {
    method: 'post',
    contentType: 'application/json',
    headers: { 'X-Sheets-Secret': SHEETS_SECRET },
    payload: JSON.stringify({
      name: 'Test Lead',
      phone: '+998900000000',
      platform: 'test',
      comment: 'testConnection() orqali yuborilgan sinov yozuvi'
    }),
    muteHttpExceptions: true
  };
  const response = UrlFetchApp.fetch(CRM_URL, options);
  Logger.log('Test natijasi: %s — %s', response.getResponseCode(), response.getContentText());
}
