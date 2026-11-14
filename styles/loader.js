/* ————————————————————————————————————————————————
   Загрузчик стилей разделов.
   Следит за body[data-race] и подключает styles/<id>.css.
   Если файла для раздела нет — генерирует палитру из id,
   поэтому любой новый раздел получает цельный стиль сразу.
   ———————————————————————————————————————————————— */
(function () {
  var STYLE_DIR = 'styles/';
  var tried = {};

  /* детерминированный оттенок из id: один и тот же id — один и тот же цвет */
  function hueOf(id) {
    var h = 0;
    for (var i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) % 360;
    return h;
  }

  function autoStyle(id) {
    if (tried['auto:' + id]) return;
    tried['auto:' + id] = true;
    var h = hueOf(id);
    var st = document.createElement('style');
    st.setAttribute('data-auto-race', id);
    st.textContent =
      'body[data-race="' + id + '"]{' +
      '--bg:hsl(' + h + ',32%,11%);' +
      '--panel:hsl(' + h + ',28%,15%);' +
      '--panel2:hsl(' + h + ',26%,19%);' +
      '--accent:hsl(' + h + ',52%,64%);' +
      '--text:hsl(' + h + ',18%,93%);}';
    document.head.appendChild(st);
  }

  function loadStyle(id) {
    if (!id || tried[id]) return;
    tried[id] = true;
    var link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = STYLE_DIR + encodeURIComponent(id) + '.css';
    link.onerror = function () { autoStyle(id); };
    document.head.appendChild(link);
  }

  var cur = document.body.getAttribute('data-race');
  loadStyle(cur);

  /* страховка на смену раздела: наблюдатель + лёгкий опрос */
  function check() {
    var now = document.body.getAttribute('data-race');
    if (now !== cur) { cur = now; loadStyle(now); }
  }
  if (window.MutationObserver) {
    new MutationObserver(check).observe(document.body, { attributes: true, attributeFilter: ['data-race'] });
  }
  setInterval(check, 250);
})();
