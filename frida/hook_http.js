// 蝶普 cicada —— qtng HTTP 层记录 agent
// 目标: 记录每个请求的 方法/URL/请求体 和 响应 状态/长度/前缀
'use strict';

const LOG_BODY = 2000;      // 请求体/响应体前缀记录字节数
const LOG_RESP = 3000;

// ---------- Qt5 容器读取 ----------
// QArrayData: ref(int32) size(int32) alloc/capacity(uint32) [pad] offset(int64) -> data
function qarrayData(ptr) {
  if (ptr.isNull()) return null;
  const d = ptr.readPointer();
  if (d.isNull()) return null;
  const size = d.add(4).readS32();
  const offset = d.add(16).readS64();
  const data = d.add(offset);
  return { size: size, data: data };
}

function readQString(strPtr) {
  const a = qarrayData(strPtr);
  if (!a || a.size < 0 || a.size > 1 << 22) return null;
  if (a.size === 0) return '';
  return a.data.readUtf16String(a.size);
}

function readQByteArray(baPtr) {
  const a = qarrayData(baPtr);
  if (!a) return null;
  if (a.size < 0 || a.size > 1 << 28) return null;
  return a.size === 0 ? '' : a.data.readByteArray(a.size);
}

function preview(buf, n) {
  if (!buf) return null;
  const b = new Uint8Array(buf);
  const head = b.slice(0, n);
  let s = '';
  for (let i = 0; i < head.length; i++) {
    const c = head[i];
    s += (c >= 32 && c < 127) ? String.fromCharCode(c) : (c === 10 ? '\\n' : '.');
  }
  return s;
}

function sha256(buf) {
  if (!buf) return null;
  try {
    const md = new Checksum('sha256');
    md.update(buf);
    return md.digest().map(b => b.toString(16).padStart(2, '0')).join('');
  } catch (e) { return null; }
}

// QUrl -> QString (sret)
let QUrl_toString = null;
function urlToString(urlPtr) {
  if (!QUrl_toString) return null;
  try {
    const out = Memory.alloc(16);
    QUrl_toString(out, urlPtr, 0);
    return readQString(out);
  } catch (e) { return null; }
}

const seen = {};

function log(o) { send(o); }

function hookFn(name, onEnter) {
  let addr = null;
  try { addr = Module.getExportByName(null, name); } catch (e) {}
  if (!addr) {
    for (const m of Process.enumerateModules()) {
      try { addr = Module.getExportByName(m.name, name); if (addr) break; } catch (e) {}
    }
  }
  if (!addr) { log({ kind: 'miss', name: name }); return false; }
  try {
    Interceptor.attach(addr, { onEnter: onEnter });
    log({ kind: 'hooked', name: name, addr: addr.toString() });
    return true;
  } catch (e) {
    log({ kind: 'hookfail', name: name, err: String(e) });
    return false;
  }
}

function main() {
  try {
    QUrl_toString = new NativeFunction(
      Module.getExportByName('QtCore', '_ZNK4QUrl8toStringE12QUrlTwoFlagsINS_19UrlFormattingOptionENS_25ComponentFormattingOptionEE'),
      'void', ['pointer', 'pointer', 'int'], 'sysv');
    log({ kind: 'info', msg: 'QUrl::toString resolved' });
  } catch (e) {
    log({ kind: 'warn', msg: 'QUrl::toString unavailable: ' + e });
  }

  // ---- HttpSession::get(QString) 及其重载 ----
  const getOverloads = [
    '_ZN4qtng11HttpSession3getERK7QString',
    '_ZN4qtng11HttpSession3getERK7QStringRK4QMapIS1_S1_E',
    '_ZN4qtng11HttpSession3getERK7QStringRK4QMapIS1_S1_ERKS4_IS1_10QByteArrayE',
    '_ZN4qtng11HttpSession3getERK7QStringRK9QUrlQuery',
    '_ZN4qtng11HttpSession3getERK7QStringRK9QUrlQueryRK4QMapIS1_10QByteArrayE',
  ];
  for (const n of getOverloads) {
    hookFn(n, function (args) {
      const u = readQString(args[0]);
      log({ kind: 'req', method: 'GET', url: u, overload: n });
    });
  }

  // ---- HttpSession::post(QString, ...) ----
  const postOverloads = [
    '_ZN4qtng11HttpSession4postERK7QStringRK10QByteArray',
    '_ZN4qtng11HttpSession4postERK7QStringRK10QByteArrayRK4QMapIS1_S4_E',
    '_ZN4qtng11HttpSession4postERK7QStringRK9QUrlQuery',
    '_ZN4qtng11HttpSession4postERK7QStringRK9QUrlQueryRK4QMapIS1_10QByteArrayE',
    '_ZN4qtng11HttpSession4postERK7QStringRK11QJsonObject',
    '_ZN4qtng11HttpSession4postERK7QStringRK11QJsonObjectRK4QMapIS1_10QByteArrayE',
  ];
  for (const n of postOverloads) {
    hookFn(n, function (args) {
      const u = readQString(args[0]);
      let body = null;
      try {
        const ba = readQByteArray(args[1]);
        if (ba) body = preview(ba, LOG_BODY);
      } catch (e) {}
      log({ kind: 'req', method: 'POST', url: u, body_preview: body, overload: n });
    });
  }

  // ---- HttpRequest::setUrl (QUrl) / setMethod / setBody ----
  hookFn('_ZN4qtng11HttpRequest6setUrlERK4QUrl', function (args) {
    const u = urlToString(args[0]);
    log({ kind: 'seturl', url: u });
  });
  hookFn('_ZN4qtng11HttpRequest9setMethodERK7QString', function (args) {
    log({ kind: 'setmethod', method: readQString(args[0]) });
  });
  hookFn('_ZN4qtng11HttpRequest7setBodyERK10QByteArray', function (args) {
    const ba = readQByteArray(args[0]);
    log({ kind: 'setreqbody', len: ba ? ba.byteLength : -1, preview: preview(ba, LOG_BODY) });
  });

  // ---- HttpResponse::setBody ----
  hookFn('_ZN4qtng12HttpResponse7setBodyERK10QByteArray', function (args) {
    let info = { kind: 'respbody' };
    try {
      const ba = readQByteArray(args[0]);
      if (ba) {
        info.len = ba.byteLength;
        info.sha256 = sha256(ba);
        info.preview = preview(ba, LOG_RESP);
      }
    } catch (e) { info.err = String(e); }
    log(info);
  });

  // ---- 顺带记录状态码 ----
  hookFn('_ZN4qtng12HttpResponse13setStatusCodeEi', function (args) {
    log({ kind: 'status', code: args[0].toInt32() });
  });
}

setImmediate(main);
