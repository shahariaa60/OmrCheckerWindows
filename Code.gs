/**
 * ব্যবধান শিক্ষালয় — OMR -> Google Sheet Webhook (Multi-Tab per Class + Fast Batch Sync)
 *
 * সুবিধা:
 * 1) প্রতিটি শ্রেণির জন্য আলাদা ট্যাব (যেমন: "৬ষ্ঠ শ্রেণি", "৯ম শ্রেণি", "১০ম শ্রেণি")।
 *    ট্যাবটি না থাকলে অটোমেটিক তৈরি হয়ে Row 1-এ "Roll" ও "Student Name" হেডার বসে যাবে।
 * 2) Batch Sync সাপোর্ট: একবারে ২৫–৫০ জন শিক্ষার্থীর নম্বর এক রিকোয়েস্টেই আপডেট হয় (সুপার ফাস্ট)।
 * 3) Single Sync (আগের ফরম্যাট)-ও আগের মতোই কাজ করবে।
 *
 * ডিপ্লয় নিয়ম:
 * Google Sheet -> Extensions -> Apps Script -> এই কোড পেস্ট করুন -> Save
 * -> Deploy -> New deployment -> Web app (Execute as: Me, Who has access: Anyone)
 * -> প্রাপ্ত /exec URL-টি Windows অ্যাপে বসিয়ে দিন।
 */

function toAscii_(v) {
  return String(v == null ? '' : v)
    .trim()
    .replace(/[০-৯]/g, function (d) { return '০১২৩৪৫৬৭৮৯'.indexOf(d); });
}

function stripZeros_(v) {
  return toAscii_(v).replace(/^0+(?=\d)/, '');
}

function json_(o) {
  return ContentService
    .createTextOutput(JSON.stringify(o))
    .setMimeType(ContentService.MimeType.JSON);
}

function getOrCreateClassSheet_(ss, className) {
  var name = String(className || '').trim();
  if (!name) return ss.getSheets()[0];

  var sheets = ss.getSheets();
  var lower = name.toLowerCase();
  for (var i = 0; i < sheets.length; i++) {
    if (sheets[i].getName().trim().toLowerCase() === lower) {
      return sheets[i];
    }
  }
  // ট্যাব না থাকলে নতুন ট্যাব তৈরি করে হেডার বসিয়ে দেবে
  var sh = ss.insertSheet(name);
  sh.getRange(1, 1, 1, 2).setValues([['Roll', 'Student Name']]);
  sh.getRange(1, 1, 1, 2).setFontWeight('bold');
  sh.setFrozenRows(1);
  return sh;
}

function getOrCreateSubjectCol_(sh, subject) {
  var lastCol = Math.max(sh.getLastColumn(), 2);
  var headers = sh.getRange(1, 1, 1, lastCol).getDisplayValues()[0].map(function (h) {
    return String(h || '').trim().toLowerCase();
  });
  var col = headers.indexOf(subject.toLowerCase()) + 1;
  if (!col) {
    col = 2;
    for (var i = 0; i < headers.length; i++) {
      if (headers[i]) col = i + 1;
    }
    col = col + 1;
    sh.getRange(1, col).setValue(subject).setFontWeight('bold');
  }
  return col;
}

function doGet() {
  return json_({ status: 'ok', message: 'Byabodhan OMR Multi-Tab Batch Webhook is running' });
}

function doPost(e) {
  var lock = LockService.getScriptLock();
  try {
    lock.waitLock(28000);
    var d = JSON.parse(e.postData.contents);
    var className = String(d.class_name || d.sheet_name || '').trim();
    var subject = String(d.subject || '').trim();
    if (!subject) return json_({ status: 'error', message: 'subject is required' });

    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var sh = getOrCreateClassSheet_(ss, className);
    var col = getOrCreateSubjectCol_(sh, subject);

    var lastRow = sh.getLastRow();
    // Roll -> { rowIdx (0-based in data), studentName } ম্যাপ তৈরি
    var rollMap = {};
    var colValues = [];
    if (lastRow >= 2) {
      var ab = sh.getRange(2, 1, lastRow - 1, 2).getDisplayValues();
      colValues = sh.getRange(2, col, lastRow - 1, 1).getValues();
      for (var i = 0; i < ab.length; i++) {
        var rKey = stripZeros_(ab[i][0]);
        if (rKey) {
          rollMap[rKey] = { idx: i, name: String(ab[i][1] || '').trim() };
        }
      }
    }

    // ---- Mode A: Batch Sync ({ class_name, subject, items: [{roll, marks}, ...] }) ----
    if (Array.isArray(d.items)) {
      var results = [];
      var changed = false;
      for (var k = 0; k < d.items.length; k++) {
        var it = d.items[k];
        var rk = stripZeros_(it.roll);
        if (!rk || !rollMap[rk]) {
          results.push({ roll: String(it.roll || ''), status: 'not_found', student_name: '' });
          continue;
        }
        var hit = rollMap[rk];
        colValues[hit.idx][0] = Number(it.marks);
        changed = true;
        results.push({ roll: String(it.roll || ''), status: 'success', student_name: hit.name, row: hit.idx + 2 });
      }
      if (changed && lastRow >= 2) {
        sh.getRange(2, col, lastRow - 1, 1).setValues(colValues);
      }
      return json_({ status: 'ok', sheet: sh.getName(), subject: subject, results: results });
    }

    // ---- Mode B: Single Sync ({ class_name, roll, subject, marks }) ----
    var singleRoll = stripZeros_(d.roll);
    if (!singleRoll) return json_({ status: 'error', message: 'roll is required' });
    if (!rollMap[singleRoll]) {
      return json_({ status: 'not_found', sheet: sh.getName() });
    }
    var m = rollMap[singleRoll];
    sh.getRange(m.idx + 2, col).setValue(Number(d.marks));
    return json_({ status: 'success', sheet: sh.getName(), student_name: m.name, row: m.idx + 2 });
  } catch (err) {
    return json_({ status: 'error', message: String(err) });
  } finally {
    try { lock.releaseLock(); } catch (x) {}
  }
}
