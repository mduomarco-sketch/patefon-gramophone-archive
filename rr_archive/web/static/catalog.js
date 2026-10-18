/* Patefon - vintage record streaming app.
 * Shared by the local dashboard (live API adapter) and the standalone gift
 * export (embedded-JSON adapter). Scraped text always goes through
 * textContent; only the constant icon SVGs below are inserted as markup.
 *
 * Adapter contract:
 *   items(): Promise<item[]>         detail(id): Promise<item + description>
 *   mediaUrl(d) labelUrl(d, i) pdfUrl(d)   optional: inspect(id), generatedAt
 */
(function () {
  "use strict";

  // ---------------------------------------------------------------------------
  // Utilities
  // ---------------------------------------------------------------------------
  const NS = "patefon:v1:";
  const store = {
    get(key, fallback) {
      try { const v = localStorage.getItem(NS + key); return v == null ? fallback : JSON.parse(v); }
      catch { return fallback; }
    },
    set(key, value) { try { localStorage.setItem(NS + key, JSON.stringify(value)); } catch { /* private mode */ } },
  };

  function el(tag, attrs, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") n.className = v;
      else if (k === "style") n.style.cssText = v;
      else if (k.startsWith("on") && typeof v === "function") n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat(Infinity)) {
      if (kid == null || kid === false) continue;
      n.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return n;
  }
  const norm = (s) => (s || "").toString().toLowerCase().replace(/ё/g, "е");
  const fmtTime = (s) => {
    if (!isFinite(s) || s < 0) return "0:00";
    const m = Math.floor(s / 60), r = Math.floor(s % 60);
    return `${m}:${String(r).padStart(2, "0")}`;
  };
  function shuffled(list) {
    const a = list.slice();
    for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; }
    return a;
  }
  function hash(str) {
    let h = 2166136261;
    for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); }
    return h >>> 0;
  }
  const uid = () => "pl-" + Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
  // replaceChildren() would print "null" for optional parts; drop them first.
  const put = (node, ...kids) => node.replaceChildren(...kids.flat().filter((k) => k != null && k !== false));

  // Constant icon markup (never data).
  const ICONS = {
    play: '<path d="M7 4.5v15l12-7.5z" fill="currentColor"/>',
    pause: '<path d="M6.5 4.5h4v15h-4zM13.5 4.5h4v15h-4z" fill="currentColor"/>',
    next: '<path d="M5 5v14l9.5-7zM16 5h2.5v14H16z" fill="currentColor"/>',
    prev: '<path d="M19 5v14L9.5 12zM5.5 5H8v14H5.5z" fill="currentColor"/>',
    shuffle: '<path d="M4 7h3.2c1.6 0 3 .8 3.9 2.1l2.8 4.3c.9 1.3 2.3 2.1 3.9 2.1H20M4 17h3.2c1.2 0 2.3-.5 3.1-1.3M13.7 8.3c.8-.8 1.9-1.3 3.1-1.3H20M17.5 4.5 20 7l-2.5 2.5M17.5 14.5 20 17l-2.5 2.5" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>',
    repeat: '<path d="M5 11V9.5A2.5 2.5 0 0 1 7.5 7H19M16.5 4.5 19 7l-2.5 2.5M19 13v1.5a2.5 2.5 0 0 1-2.5 2.5H5M7.5 19.5 5 17l2.5-2.5" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>',
    heart: '<path d="M12 20s-7.5-4.6-7.5-10.1A4.4 4.4 0 0 1 12 7.3a4.4 4.4 0 0 1 7.5 2.6C19.5 15.4 12 20 12 20z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/>',
    heartFill: '<path d="M12 20s-7.5-4.6-7.5-10.1A4.4 4.4 0 0 1 12 7.3a4.4 4.4 0 0 1 7.5 2.6C19.5 15.4 12 20 12 20z" fill="currentColor"/>',
    plus: '<path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>',
    queue: '<path d="M4 6h12M4 11h12M4 16h7M16 14.5v5l4-2.5z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>',
    volume: '<path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z" fill="currentColor"/><path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>',
    mute: '<path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z" fill="currentColor"/><path d="M16 9.5l5 5M21 9.5l-5 5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>',
    close: '<path d="M6 6l12 12M18 6 6 18" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>',
    search: '<circle cx="11" cy="11" r="6.5" fill="none" stroke="currentColor" stroke-width="1.7"/><path d="m16 16 4 4" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>',
    grid: '<path d="M4.5 4.5h6v6h-6zM13.5 4.5h6v6h-6zM4.5 13.5h6v6h-6zM13.5 13.5h6v6h-6z" fill="none" stroke="currentColor" stroke-width="1.6"/>',
    list: '<path d="M8 6.5h12M8 12h12M8 17.5h12M4 6.5h.01M4 12h.01M4 17.5h.01" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>',
    menu: '<path d="M4 7h16M4 12h16M4 17h16" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>',
    more: '<circle cx="5.5" cy="12" r="1.6" fill="currentColor"/><circle cx="12" cy="12" r="1.6" fill="currentColor"/><circle cx="18.5" cy="12" r="1.6" fill="currentColor"/>',
    book: '<path d="M5 4.5h9.5A3.5 3.5 0 0 1 18 8v11.5H8.5A3.5 3.5 0 0 1 5 16z M18 19.5H8.5A3.5 3.5 0 0 1 5 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/>',
    vinyl: '<circle cx="12" cy="12" r="8.5" fill="none" stroke="currentColor" stroke-width="1.6"/><circle cx="12" cy="12" r="5.5" fill="none" stroke="currentColor" stroke-width="0.9" opacity=".6"/><circle cx="12" cy="12" r="2.2" fill="currentColor"/><path d="M17.5 3.5 21 7" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>',
    nAll: '<path d="M4 18V6l8 4.5L20 6v12" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="miter"/>',
    nRadio: '<rect x="3.5" y="8" width="17" height="11" fill="none" stroke="currentColor" stroke-width="2"/><path d="M7 8 15 3.5" stroke="currentColor" stroke-width="2"/><circle cx="15.5" cy="13.5" r="2.6" fill="currentColor"/><path d="M6.5 11.5h4M6.5 15h4" stroke="currentColor" stroke-width="1.6"/>',
    nLiked: '<path d="M12 20 4.2 11.6a4.4 4.4 0 0 1 7.8-4 4.4 4.4 0 0 1 7.8 4z" fill="currentColor"/>',
    nRecent: '<circle cx="12" cy="12" r="8" fill="none" stroke="currentColor" stroke-width="2"/><path d="M12 7v5l3.5 2" stroke="currentColor" stroke-width="2" fill="none"/>',
    nStar: '<path d="M12 2.8l2.6 6.3 6.8.5-5.2 4.4 1.7 6.7L12 17l-5.9 3.7 1.7-6.7-5.2-4.4 6.8-.5z" fill="currentColor"/>',
    nLang: '<path d="M12 3 21 12 12 21 3 12z" fill="none" stroke="currentColor" stroke-width="2"/><path d="M12 8 16 12 12 16 8 12z" fill="currentColor"/>',
    nSpoken: '<rect x="9" y="3" width="6" height="11" fill="currentColor"/><path d="M6 11a6 6 0 0 0 12 0M12 17v4M8 21h8" fill="none" stroke="currentColor" stroke-width="2"/>',
    nGallery: '<path d="M5 3h10l4 4v14H5z" fill="none" stroke="currentColor" stroke-width="2"/><path d="M8 11h8M8 14.5h8M8 18h5" stroke="currentColor" stroke-width="1.6"/>',
    external: '<path d="M14 5h5v5M19 5l-8 8M17 14v4.5a1.5 1.5 0 0 1-1.5 1.5h-10A1.5 1.5 0 0 1 4 18.5v-10A1.5 1.5 0 0 1 5.5 7H10" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
  };
  function icon(name, label) {
    const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    s.setAttribute("viewBox", "0 0 24 24");
    s.setAttribute("aria-hidden", "true");
    s.innerHTML = ICONS[name];
    return label ? [s, el("span", { class: "sr-only" }, label)] : s;
  }

  // ---------------------------------------------------------------------------
  // Interface language: EN | RU | KZ
  // ---------------------------------------------------------------------------
  const I18N = {
    en: {
      brandSub: "Vintage record archive", library: "Listen", collections: "Collections", playlists: "Your playlists",
      all: "All playable music", radio: "Endless radio mix", liked: "Liked songs", recent: "Recently played",
      kazakh: "Kazakh & Central Asian spotlight", languages: "Browse by language", spoken: "Radio & spoken word",
      gallery: "Print & label gallery", newPlaylist: "New playlist", backup: "Back up library", restore: "Restore",
      search: "Search records, artists, composers, catalogue numbers…", playAll: "Play all", shuffleAll: "Shuffle collection",
      startRadio: "Start radio", grid: "Grid", table: "List", tracks: (n) => `${n} ${n === 1 ? "record" : "records"}`,
      docs: (n) => `${n} ${n === 1 ? "item" : "items"}`,
      heroAll: "Shellac, lacquer and wax - restored and ready to play.",
      heroRadio: "Every playable record, shuffled into one unbroken broadcast. It reshuffles itself when it runs out.",
      heroLiked: "The records you have kept close.", heroRecent: "Your listening, most recent first.",
      heroKazakh: "Recordings in Kazakh and the Turkic languages of the steppe, and music from across Central Asia.",
      heroLanguages: "The archive by language and tradition.", heroSpoken: "Speeches, broadcasts, readings and voices of the century.",
      heroGallery: "A quiet reading room: books, periodicals, catalogues and label scans.",
      heroPlaylist: "Your playlist", play: "Play", pause: "Pause", next: "Next", prev: "Previous",
      shuffle: "Shuffle", repeatOff: "Repeat off", repeatAll: "Repeat all", repeatOne: "Repeat one",
      surprise: "Surprise me", queueTitle: "Up next", nowPlaying: "Now playing", queueEmpty: "Nothing queued yet.",
      clearQueue: "Clear queue", volume: "Volume", mute: "Mute", unmute: "Unmute", speed: "Turntable speed",
      like: "Add to liked songs", unlike: "Remove from liked songs", addToPlaylist: "Add to playlist", addToQueue: "Add to queue",
      playNext: "Play next", removeFromPlaylist: "Remove from this playlist", remove: "Remove", rename: "Rename",
      deletePl: "Delete playlist", exportPl: "Export", create: "Create", cancel: "Cancel", save: "Save",
      plName: "Playlist name", plDefault: "My playlist", confirmDelete: (n) => `Delete “${n}”? The records stay in the archive.`,
      story: "Archival story", openStory: "Open the record's story", catalog: "Catalogue no.", matrix: "Matrix",
      recorded: "Recorded", composer: "Composer", lyricist: "Lyrics", conductor: "Conductor", accompaniment: "Accompaniment",
      label: "Label", collection: "Collection", format: "Format", size: "Record size", rpm: "Transfer speed",
      onLabel: "Printed on the label", notes: "Archive notes", source: "View on russian-records.com",
      openPdf: "Open PDF", readHere: "Read here", hidePdf: "Hide PDF", zoom: "Click to zoom",
      emptyAll: "No playable records yet. They will appear here as the archive grows.",
      emptyLiked: "Tap the heart on any record to keep it here.", emptyRecent: "Records you play will appear here.",
      emptySearch: "Nothing matches that search.", emptyPlaylist: "This playlist is empty. Use + on any record to add it.",
      kzWelcomeTitle: "Қош келдіңіз - welcome",
      kzWelcome: "No Kazakh or Central Asian recordings have been archived yet. As the archive finds them, they will appear here on their own.",
      emptyGallery: "No books, periodicals or scans have been archived yet.", emptySpoken: "No radio or spoken-word recordings yet.",
      choose: "Choose a record to begin", added: (n) => `Added to “${n}”`, queued: "Added to the queue", playingNext: "Will play next",
      imported: (n) => `Restored ${n} playlists`, importFailed: "That file is not a Patefon backup.",
      cantPlay: "This record could not be played - skipping.", liveNew: (n) => `${n} new records`, refresh: "Refresh",
      shortcuts: "Space play · N next · P previous · S shuffle · R repeat · M mute · / search", disc78: "78 rpm", disc33: "33⅓ rpm", discSlow: "Slow",
      generated: (d) => `Catalogue compiled ${d}`, colTitle: "Title", colLang: "Language", colYear: "Year",
      dedicationBtn: "Dedication 💌", dedTitle: "Patefon: A Journey Through Time", dedTitleName: (n) => `A Gift for You, ${n}`,
      dedHeader: "Telegram · Телеграмма · Жеделхат", dedOpen: "Open the Gramophone 🎵", dedSign: "With love,",
      dedMessage: "This little archive was gathered just for you: shellac records, crackling radio broadcasts and melodies of the steppe, each one found, gently restored and set spinning again.\n\nSome of these voices have not been heard for almost a hundred years. Now they sing for you. Whenever you miss the old world, put on a record - it will always be waiting here.",
      tabRecord: "The record", tabNotes: "📜 Lyrics & Notes", lyricsTitle: "Lyrics", linerNotes: "Historical liner notes",
      silentNotes: "The historical parchment for this recording is silent… no notes or lyrics have survived with it yet.",
      srcWikisource: "Public domain · Wikisource", srcManual: "Added by you", work: "From the work",
    },
    ru: {
      brandSub: "Архив старых пластинок", library: "Слушать", collections: "Коллекции", playlists: "Ваши плейлисты",
      all: "Вся музыка", radio: "Бесконечное радио", liked: "Любимые", recent: "Недавно слушали",
      kazakh: "Казахстан и Центральная Азия", languages: "По языкам", spoken: "Радио и речь",
      gallery: "Печать и этикетки", newPlaylist: "Новый плейлист", backup: "Сохранить копию", restore: "Восстановить",
      search: "Поиск: записи, исполнители, композиторы, номера…", playAll: "Слушать всё", shuffleAll: "Перемешать",
      startRadio: "Включить радио", grid: "Сетка", table: "Список",
      tracks: (n) => `${n} ${plural(n, "запись", "записи", "записей")}`, docs: (n) => `${n} ${plural(n, "документ", "документа", "документов")}`,
      heroAll: "Шеллак, лак и воск - всё восстановлено и готово к прослушиванию.",
      heroRadio: "Все записи архива в одном непрерывном эфире. Когда они закончатся, плейлист перемешается заново.",
      heroLiked: "Записи, которые вам дороги.", heroRecent: "История прослушивания, сначала новые.",
      heroKazakh: "Записи на казахском и тюркских языках степи, музыка всей Центральной Азии.",
      heroLanguages: "Архив по языкам и традициям.", heroSpoken: "Речи, передачи, чтения и голоса прошлого века.",
      heroGallery: "Тихий читальный зал: книги, журналы, каталоги и этикетки.",
      heroPlaylist: "Ваш плейлист", play: "Играть", pause: "Пауза", next: "Следующий", prev: "Предыдущий",
      shuffle: "Перемешать", repeatOff: "Без повтора", repeatAll: "Повторять всё", repeatOne: "Повторять запись",
      surprise: "Удиви меня", queueTitle: "Далее", nowPlaying: "Сейчас играет", queueEmpty: "Очередь пуста.",
      clearQueue: "Очистить очередь", volume: "Громкость", mute: "Без звука", unmute: "Включить звук", speed: "Скорость проигрывателя",
      like: "В любимые", unlike: "Убрать из любимых", addToPlaylist: "В плейлист", addToQueue: "В очередь",
      playNext: "Играть следующей", removeFromPlaylist: "Убрать из плейлиста", remove: "Убрать", rename: "Переименовать",
      deletePl: "Удалить плейлист", exportPl: "Экспорт", create: "Создать", cancel: "Отмена", save: "Сохранить",
      plName: "Название плейлиста", plDefault: "Мой плейлист", confirmDelete: (n) => `Удалить «${n}»? Записи останутся в архиве.`,
      story: "История записи", openStory: "Открыть историю записи", catalog: "Каталожный №", matrix: "Матрица",
      recorded: "Запись", composer: "Композитор", lyricist: "Слова", conductor: "Дирижёр", accompaniment: "Аккомпанемент",
      label: "Фирма", collection: "Коллекция", format: "Формат", size: "Размер пластинки", rpm: "Скорость оцифровки",
      onLabel: "Надпись на этикетке", notes: "Заметки архива", source: "Открыть на russian-records.com",
      openPdf: "Открыть PDF", readHere: "Читать здесь", hidePdf: "Скрыть PDF", zoom: "Нажмите, чтобы увеличить",
      emptyAll: "Пока нет записей для прослушивания. Они появятся здесь по мере пополнения архива.",
      emptyLiked: "Нажмите на сердечко у любой записи, чтобы сохранить её здесь.", emptyRecent: "Здесь появятся прослушанные записи.",
      emptySearch: "Ничего не найдено.", emptyPlaylist: "Плейлист пуст. Добавляйте записи кнопкой +.",
      kzWelcomeTitle: "Қош келдіңіз - добро пожаловать",
      kzWelcome: "Казахских и центральноазиатских записей пока нет. Как только архив их найдёт, они сами появятся здесь.",
      emptyGallery: "Книг, журналов и сканов пока нет.", emptySpoken: "Радиозаписей и речей пока нет.",
      choose: "Выберите запись", added: (n) => `Добавлено в «${n}»`, queued: "Добавлено в очередь", playingNext: "Будет следующей",
      imported: (n) => `Восстановлено плейлистов: ${n}`, importFailed: "Это не резервная копия Patefon.",
      cantPlay: "Эту запись не удалось воспроизвести - переходим к следующей.", liveNew: (n) => `Новых записей: ${n}`, refresh: "Обновить",
      shortcuts: "Пробел - играть · N далее · P назад · S перемешать · R повтор · M звук · / поиск", disc78: "78 об/мин", disc33: "33⅓ об/мин", discSlow: "Медленно",
      generated: (d) => `Каталог составлен ${d}`, colTitle: "Название", colLang: "Язык", colYear: "Год",
      dedicationBtn: "Посвящение 💌", dedTitle: "Патефон: путешествие во времени", dedTitleName: (n) => `Подарок для тебя, ${n}`,
      dedHeader: "Телеграмма · Telegram · Жеделхат", dedOpen: "Открыть патефон 🎵", dedSign: "С любовью,",
      dedMessage: "Этот маленький архив собран специально для тебя: шеллаковые пластинки, потрескивающие радиопередачи и мелодии степи - каждую нашли, бережно восстановили и снова заставили звучать.\n\nНекоторые из этих голосов не звучали почти сто лет. Теперь они поют для тебя. Когда соскучишься по старому миру, поставь пластинку - она всегда будет ждать тебя здесь.",
      tabRecord: "Пластинка", tabNotes: "📜 Слова и заметки", lyricsTitle: "Слова", linerNotes: "Исторические заметки",
      silentNotes: "Исторический пергамент этой записи пока молчит… заметок и слов к ней ещё не нашлось.",
      srcWikisource: "Общественное достояние · Викитека", srcManual: "Добавлено вами", work: "Из произведения",
    },
    kz: {
      brandSub: "Ескі пластинкалар мұрағаты", library: "Тыңдау", collections: "Жинақтар", playlists: "Ойнату тізімдеріңіз",
      all: "Барлық музыка", radio: "Шексіз радио", liked: "Ұнаған әндер", recent: "Жақында тыңдалғандар",
      kazakh: "Қазақстан және Орталық Азия", languages: "Тілдер бойынша", spoken: "Радио және сөз",
      gallery: "Баспа және жапсырмалар", newPlaylist: "Жаңа тізім", backup: "Көшірме сақтау", restore: "Қалпына келтіру",
      search: "Іздеу: жазбалар, орындаушылар, композиторлар, нөмірлер…", playAll: "Барлығын тыңдау", shuffleAll: "Араластыру",
      startRadio: "Радионы қосу", grid: "Тор", table: "Тізім",
      tracks: (n) => `${n} жазба`, docs: (n) => `${n} құжат`,
      heroAll: "Шеллак, лак және балауыз - бәрі қалпына келтіріліп, тыңдауға дайын.",
      heroRadio: "Мұрағаттағы барлық жазба бір үздіксіз эфирде. Жазбалар біткенде, тізім қайта араласады.",
      heroLiked: "Жүрегіңізге жақын жазбалар.", heroRecent: "Тыңдау тарихы, ең жаңасы басында.",
      heroKazakh: "Қазақ және дала түркі тілдеріндегі жазбалар, бүкіл Орталық Азия музыкасы.",
      heroLanguages: "Мұрағат тілдер мен дәстүрлер бойынша.", heroSpoken: "Өткен ғасырдың сөздері, хабарлары мен дауыстары.",
      heroGallery: "Тыныш оқу залы: кітаптар, журналдар, каталогтар және жапсырмалар.",
      heroPlaylist: "Сіздің тізіміңіз", play: "Ойнату", pause: "Кідірту", next: "Келесі", prev: "Алдыңғы",
      shuffle: "Араластыру", repeatOff: "Қайталамау", repeatAll: "Бәрін қайталау", repeatOne: "Бір жазбаны қайталау",
      surprise: "Таңғалдыр", queueTitle: "Келесі", nowPlaying: "Қазір ойнап тұр", queueEmpty: "Кезек бос.",
      clearQueue: "Кезекті тазалау", volume: "Дыбыс деңгейі", mute: "Дыбыссыз", unmute: "Дыбысты қосу", speed: "Ойнатқыш жылдамдығы",
      like: "Ұнағандарға қосу", unlike: "Ұнағандардан алып тастау", addToPlaylist: "Тізімге қосу", addToQueue: "Кезекке қосу",
      playNext: "Келесі болып ойнату", removeFromPlaylist: "Тізімнен алып тастау", remove: "Алып тастау", rename: "Атын өзгерту",
      deletePl: "Тізімді жою", exportPl: "Экспорттау", create: "Құру", cancel: "Бас тарту", save: "Сақтау",
      plName: "Тізім атауы", plDefault: "Менің тізімім", confirmDelete: (n) => `«${n}» жойылсын ба? Жазбалар мұрағатта қалады.`,
      story: "Жазба тарихы", openStory: "Жазба тарихын ашу", catalog: "Каталог №", matrix: "Матрица",
      recorded: "Жазылған", composer: "Композитор", lyricist: "Сөзі", conductor: "Дирижер", accompaniment: "Сүйемелдеу",
      label: "Лейбл", collection: "Жинақ", format: "Формат", size: "Пластинка өлшемі", rpm: "Цифрлау жылдамдығы",
      onLabel: "Жапсырмадағы жазу", notes: "Мұрағат жазбалары", source: "russian-records.com сайтында ашу",
      openPdf: "PDF ашу", readHere: "Осында оқу", hidePdf: "PDF жасыру", zoom: "Үлкейту үшін басыңыз",
      emptyAll: "Тыңдауға дайын жазба әзірге жоқ. Мұрағат толыққан сайын олар осында пайда болады.",
      emptyLiked: "Кез келген жазбадағы жүрекшені басып, осында сақтаңыз.", emptyRecent: "Тыңдаған жазбаларыңыз осында көрінеді.",
      emptySearch: "Ештеңе табылмады.", emptyPlaylist: "Тізім бос. Жазбаларды + түймесімен қосыңыз.",
      kzWelcomeTitle: "Қош келдіңіз",
      kzWelcome: "Қазақ және Орталық Азия жазбалары әзірге жоқ. Мұрағат оларды тапқан сайын, осында өздігінен пайда болады.",
      emptyGallery: "Кітаптар, журналдар мен сканерлер әзірге жоқ.", emptySpoken: "Радио және сөз жазбалары әзірге жоқ.",
      choose: "Жазбаны таңдаңыз", added: (n) => `«${n}» тізіміне қосылды`, queued: "Кезекке қосылды", playingNext: "Келесі болып ойнайды",
      imported: (n) => `Қалпына келтірілген тізімдер: ${n}`, importFailed: "Бұл Patefon көшірмесі емес.",
      cantPlay: "Бұл жазбаны ойнату мүмкін болмады - келесісіне өтеміз.", liveNew: (n) => `${n} жаңа жазба`, refresh: "Жаңарту",
      shortcuts: "Бос орын - ойнату · N келесі · P алдыңғы · S араластыру · R қайталау · M дыбыс · / іздеу", disc78: "78 айн/мин", disc33: "33⅓ айн/мин", discSlow: "Баяу",
      generated: (d) => `Каталог құрастырылған күні: ${d}`, colTitle: "Атауы", colLang: "Тіл", colYear: "Жыл",
      dedicationBtn: "Арнау 💌", dedTitle: "Патефон: уақыт бойымен саяхат", dedTitleName: (n) => `Саған арналған сыйлық, ${n}`,
      dedHeader: "Жеделхат · Телеграмма · Telegram", dedOpen: "Патефонды ашу 🎵", dedSign: "Махаббатпен,",
      dedMessage: "Бұл шағын мұрағат тек саған арналып жиналды: шеллак пластинкалар, сытырлаған радиохабарлар және даланың әуендері - әрқайсысы табылып, мұқият қалпына келтіріліп, қайта айналды.\n\nБұл дауыстардың кейбірі бір ғасырға жуық естілмеген. Енді олар сен үшін шырқайды. Ескі әлемді сағынғанда, пластинканы қос - ол әрқашан осында сені күтеді.",
      tabRecord: "Пластинка", tabNotes: "📜 Сөздер мен жазбалар", lyricsTitle: "Сөздері", linerNotes: "Тарихи жазбалар",
      silentNotes: "Бұл жазбаның тарихи пергаменті әзірге үнсіз… оған қатысты жазбалар мен сөздер әлі табылмады.",
      srcWikisource: "Қоғамдық игілік · Уикикітап", srcManual: "Сіз қосқан", work: "Шығармадан",
    },
  };
  function plural(n, one, few, many) {
    const m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return one;
    if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
    return many;
  }
  const LANG_NAMES = {
    Russian: ["Русский", "Орыс"], Ukrainian: ["Украинский", "Украин"], Belarusian: ["Белорусский", "Беларусь"],
    Yiddish: ["Идиш", "Идиш"], Hebrew: ["Иврит", "Иврит"], Georgian: ["Грузинский", "Грузин"], Armenian: ["Армянский", "Армян"],
    Polish: ["Польский", "Поляк"], Romani: ["Цыганский", "Сыған"], Tatar: ["Татарский", "Татар"], Kazakh: ["Казахский", "Қазақ"],
    Uzbek: ["Узбекский", "Өзбек"], Bashkir: ["Башкирский", "Башқұрт"], Azerbaijani: ["Азербайджанский", "Әзербайжан"],
    Chuvash: ["Чувашский", "Чуваш"], German: ["Немецкий", "Неміс"], French: ["Французский", "Француз"],
    Italian: ["Итальянский", "Итальян"], English: ["Английский", "Ағылшын"], Spanish: ["Испанский", "Испан"],
    Latin: ["Латинский", "Латын"], Church_Slavonic: ["Церковнославянский", "Шіркеулік славян"],
    Latvian: ["Латышский", "Латыш"], Lithuanian: ["Литовский", "Литва"], Estonian: ["Эстонский", "Эстон"],
    Finnish: ["Финский", "Фин"], Bulgarian: ["Болгарский", "Болгар"], Serbian: ["Сербский", "Серб"], Czech: ["Чешский", "Чех"],
    Romanian: ["Румынский", "Румын"], Greek: ["Греческий", "Грек"], Hungarian: ["Венгерский", "Венгр"],
    Instrumental: ["Инструментальная", "Аспаптық"], Unknown_Language: ["Язык не определён", "Тілі белгісіз"],
  };

  const CENTRAL_ASIA_LANGS = new Set(["Kazakh", "Tatar", "Uzbek", "Bashkir", "Azerbaijani", "Chuvash", "Kyrgyz", "Turkmen", "Uyghur"]);
  const CENTRAL_ASIA_RE = /kazakh|қазақ|казах|kirgiz|kyrgyz|киргиз|кыргыз|turkic|тюрк|tatar|татар|uzbek|узбек|bashkir|башкир|turkmen|туркм|uyghur|uighur|уйгур|central asia|средн\w* ази|туркестан|turkestan|almaty|алма-ата|алматы|верный|semipalatinsk|семипалатинск|astana|астана|tashkent|ташкент|bukhara|бухар|samarkand|самарканд|kazan|казан[ьи]|steppe|степ[ьи]|dombra|домбр|kobyz|кобыз|abai|абай|kuliash|күләш|куляш|байсеитова|baiseitova|zhambyl|жамбыл|kurmangazy|курмангазы|құрманғазы|akyn|акын/i;

  // ---------------------------------------------------------------------------
  // App
  // ---------------------------------------------------------------------------
  function mount(root, adapter) {
    const S = {
      items: [], byId: new Map(), view: store.get("view", "all"), arg: store.get("viewArg", null),
      q: "", layout: store.get("layout", "grid"), ui: store.get("ui", "en"), shown: 120, sort: store.get("sort", "title"),
      liked: new Set(store.get("liked", [])), recent: store.get("recent", []), playlists: store.get("playlists", []),
      base: [], order: [], pos: -1, currentId: null, shuffle: store.get("shuffle", false),
      repeat: store.get("repeat", "off"), radio: false, speed: store.get("speed", 1),
      volume: store.get("volume", 0.85), muted: false, storyTab: store.get("storyTab", "record"), rpm: store.get("spinRpm", 8),   // visual spin; 8 = calm default errors: 0, focusedId: null,
    };
    if (!I18N[S.ui]) S.ui = "en";

    // Curated starting library baked into the gift (export "seed"). Merged once
    // per seed version; what she removes herself is remembered and never comes
    // back with a later re-export.
    const seed = adapter.seed && typeof adapter.seed === "object" ? adapter.seed : null;
    const seedPlIds = new Set(seed ? (seed.playlists || []).map((p) => String(p.id)) : []);
    const seedLiked = new Set(seed ? (seed.liked || []).map(String) : []);
    const seedGone = store.get("seedGone", { playlists: [], liked: [], tracks: {} });
    function rememberGone(kind, id, plId) {
      if (kind === "playlist" && seedPlIds.has(id)) seedGone.playlists.push(id);
      else if (kind === "liked" && seedLiked.has(id)) seedGone.liked.push(id);
      else if (kind === "track" && seedPlIds.has(plId)) (seedGone.tracks[plId] = seedGone.tracks[plId] || []).push(id);
      else return;
      store.set("seedGone", seedGone);
    }
    if (seed && seed.version && store.get("seedVersion", null) !== seed.version) {
      for (const sp of seed.playlists || []) {
        const id = String(sp.id);
        if (seedGone.playlists.includes(id)) continue;
        const removed = new Set(seedGone.tracks[id] || []);
        const tracks = (sp.tracks || []).map(String).filter((tr) => !removed.has(tr));
        const mine = S.playlists.find((p) => p.id === id);
        if (mine) mine.tracks = [...mine.tracks, ...tracks.filter((tr) => !mine.tracks.includes(tr))];
        else S.playlists.push({ id, name: String(sp.name || "Playlist"), tracks, created: sp.created || "", gift: true });
      }
      for (const id of seedLiked) if (!seedGone.liked.includes(id)) S.liked.add(id);
      store.set("seedVersion", seed.version);
      store.set("liked", [...S.liked]); store.set("playlists", S.playlists);
    }
    const t = (k, ...a) => { const v = I18N[S.ui][k] ?? I18N.en[k]; return typeof v === "function" ? v(...a) : v; };
    const langName = (l) => { const n = LANG_NAMES[l]; return S.ui === "ru" && n ? n[0] : S.ui === "kz" && n ? n[1] : (l || "").replace(/_/g, " "); };
    const nativeName = (l) => { const n = LANG_NAMES[l]; return n ? (S.ui === "ru" ? n[1] : n[0]) : ""; };

    const audio = new Audio();
    audio.preload = "metadata";
    audio.preservesPitch = false; audio.mozPreservesPitch = false; audio.webkitPreservesPitch = false;
    audio.volume = S.volume;

    // --- item predicates -------------------------------------------------------
    const AUDIO_EXT = /\.(mp3|flac|ogg|oga|wav|m4a|opus)$/i;
    const isPlayable = (it) => it.status === "COMPLETED" && it.file && AUDIO_EXT.test(it.file)
      && (it.branch === "MUSIC" || it.branch === "RADIO_SPEECH");
    const isDoc = (it) => it.status === "COMPLETED" && it.file && (it.branch === "PRINT" || it.branch === "IMAGE");
    const isCentralAsian = (it) => CENTRAL_ASIA_LANGS.has(it.language)
      || CENTRAL_ASIA_RE.test([it.title, it.artist, it.composer, it.category_path, it.note, it.description].join(" "));
    const hay = (it) => it._hay || (it._hay = norm([it.title, it.artist, it.composer, it.category_path, it.record_label,
      it.catalog_number, it.matrix_number, it.language, langName(it.language), it.year_raw, it.description].join(" ")));
    const matchesQ = (it) => !S.q || norm(S.q).split(/\s+/).every((w) => !w || hay(it).includes(w));
    const playable = () => S.items.filter(isPlayable);
    const get = (id) => S.byId.get(id);
    const ext = (it) => ((it.file || "").match(/\.(\w+)$/) || [, ""])[1].toUpperCase();

    // --- persistence --------------------------------------------------------------
    const saveLib = () => {
      store.set("liked", [...S.liked]); store.set("recent", S.recent.slice(0, 150)); store.set("playlists", S.playlists);
    };

    // --- shell ---------------------------------------------------------------------
    const side = el("nav", { class: "pf-side", "aria-label": "Library" });
    const topbar = el("div", { class: "pf-topbar" });
    const hero = el("header", { class: "pf-hero" });
    const content = el("div", { class: "pf-content" });
    const main = el("main", { class: "pf-main", id: "pf-main" }, topbar, hero, content);
    const player = el("footer", { class: "pf-player", "aria-label": "Player" });
    const scrim = el("div", { class: "pf-scrim", onclick: () => closeAll() });
    const story = el("aside", { class: "pf-drawer", "aria-label": "Record story", "aria-hidden": "true" });
    const queueDrawer = el("aside", { class: "pf-drawer", "aria-label": "Queue", "aria-hidden": "true" });
    const toastEl = el("div", { class: "pf-toast", role: "status", "aria-live": "polite" });
    const slot = el("div", { class: "pf-slot", id: "pf-header-slot" });
    const app = el("div", { class: "pf-app" }, side, main, player);
    root.replaceChildren(app, scrim, story, queueDrawer, toastEl);

    let toastTimer = 0;
    function toast(msg) {
      toastEl.textContent = msg; toastEl.classList.add("show");
      clearTimeout(toastTimer); toastTimer = setTimeout(() => toastEl.classList.remove("show"), 2600);
    }

    // --- discs ----------------------------------------------------------------------
    // Label scans are hand-made photos, so the record's spindle hole is often
    // not the photo's centre. fit = [cx, cy, scale] (rr_archive/labelfit.py)
    // shifts the photo so the hole sits on the spindle we rotate around.
    function fitTransform(fit) {
      if (!fit || (fit[0] === 0.5 && fit[1] === 0.5)) return "scale(1.02)";
      const [cx, cy, s] = fit;
      return `scale(${(s * 1.02).toFixed(4)}) translate(${((0.5 - cx) * 100).toFixed(2)}%, ${((0.5 - cy) * 100).toFixed(2)}%)`;
    }
    function disc(it, opts = {}) {
      const label = it && it.labels && it.labels.length ? adapter.labelUrl(it, 0) : null;
      const img = label ? el("img", { src: label, alt: "", loading: opts.eager ? "eager" : "lazy", decoding: "async",
        "data-fit-for": it.id, style: `transform:${fitTransform(it.fit)}`,
        onerror: (e) => { e.target.remove(); },
        onload: (e) => { if (!it.fit) measureFit(it, e.target); } }) : null;
      const lab = el("div", { class: "disc-label" },
        img || el("span", { class: "fallback" }, it ? (it.record_label || it.title || "").slice(0, 28) : ""));
      return el("div", { class: "disc" + (label ? " has-scan" : ""), "data-track": it ? it.id : "" },
        el("div", { class: "disc-rot" }, lab, el("div", { class: "disc-dust", "aria-hidden": "true" })),
        el("div", { class: "disc-hole" }));
    }

    // Records downloaded after the last labelfit run are measured here, in the
    // browser, with the same radial-symmetry vote. Only possible when the scan
    // is same-origin (the dashboard); file:// canvases are tainted, which is why
    // the gift carries pre-computed fits instead.
    const fitQueue = [];
    let fitBusy = false;
    function measureFit(it, imgEl) {
      if (!/^https?:$/.test(location.protocol) || it.fit || it._fitQueued) return;
      const cached = store.get("fit:" + it.id, null);
      if (cached) { applyFit(it, cached); return; }
      it._fitQueued = true;
      fitQueue.push([it, imgEl]);
      if (!fitBusy) pumpFits();
    }
    function pumpFits() {
      const job = fitQueue.shift();
      if (!job) { fitBusy = false; return; }
      fitBusy = true;
      const idle = window.requestIdleCallback || ((fn) => setTimeout(fn, 30));
      idle(() => {
        const [it, imgEl] = job;
        try {
          const fit = symmetryFit(imgEl);
          store.set("fit:" + it.id, fit);
          applyFit(it, fit);
        } catch { it.fit = [0.5, 0.5, 1]; }
        pumpFits();
      });
    }
    function applyFit(it, fit) {
      it.fit = fit;
      document.querySelectorAll(`img[data-fit-for="${CSS.escape(it.id)}"]`).forEach((n) => { n.style.transform = fitTransform(fit); });
    }
    function symmetryFit(imgEl) {
      const N = 128;
      const iw = imgEl.naturalWidth, ih = imgEl.naturalHeight;
      const side = Math.min(iw, ih);
      const cv = document.createElement("canvas");
      cv.width = N; cv.height = N;
      const g = cv.getContext("2d", { willReadFrequently: true });
      // the visible square (object-fit: cover)
      g.drawImage(imgEl, (iw - side) / 2, (ih - side) / 2, side, side, 0, 0, N, N);
      const px = g.getImageData(0, 0, N, N).data;  // throws on tainted canvas
      const L = new Float32Array(N * N);
      for (let i = 0; i < N * N; i++) L[i] = 0.299 * px[i * 4] + 0.587 * px[i * 4 + 1] + 0.114 * px[i * 4 + 2];
      const gx = new Float32Array(N * N), gy = new Float32Array(N * N), mag = new Float32Array(N * N);
      for (let y = 1; y < N - 1; y++) for (let x = 1; x < N - 1; x++) {
        const i = y * N + x;
        gx[i] = (L[i - N + 1] + 2 * L[i + 1] + L[i + N + 1]) - (L[i - N - 1] + 2 * L[i - 1] + L[i + N - 1]);
        gy[i] = (L[i + N - 1] + 2 * L[i + N] + L[i + N + 1]) - (L[i - N - 1] + 2 * L[i - N] + L[i - N + 1]);
        mag[i] = Math.hypot(gx[i], gy[i]);
      }
      const sorted = Float32Array.from(mag).sort();
      const thr = Math.max(sorted[Math.floor(sorted.length * 0.88)], 1e-3);
      const acc = new Float32Array(N * N);
      const lo = N * 0.25, hi = N * 0.75, R = N * 0.55;
      for (let i = 0; i < N * N; i++) {
        if (mag[i] <= thr) continue;
        const x = i % N, y = (i / N) | 0, ux = gx[i] / mag[i], uy = gy[i] / mag[i];
        for (let t = -R; t <= R; t++) {
          if (t > -4 && t < 4) continue;
          const vx = x + ux * t, vy = y + uy * t;
          if (vx >= lo && vx < hi && vy >= lo && vy < hi) acc[(vy | 0) * N + (vx | 0)] += 1;
        }
      }
      let best = 0, bi = 0, sum = 0, cnt = 0;
      for (let y = lo | 0; y < hi; y++) for (let x = lo | 0; x < hi; x++) {
        let v = 0;   // 5x5 box blur at (x, y)
        for (let dy = -2; dy <= 2; dy++) for (let dx = -2; dx <= 2; dx++) v += acc[(y + dy) * N + (x + dx)] || 0;
        sum += v; cnt++;
        if (v > best) { best = v; bi = y * N + x; }
      }
      const conf = best / (sum / cnt + 1e-6);
      const cx = ((bi % N) + 0.5) / N, cy = (((bi / N) | 0) + 0.5) / N;
      const off = Math.max(Math.abs(cx - 0.5), Math.abs(cy - 0.5));
      if (conf < 4 || off < 0.006 || cx < 0.2 || cx > 0.8 || cy < 0.2 || cy > 0.8) return [0.5, 0.5, 1];
      return [+cx.toFixed(4), +cy.toFixed(4), +Math.min(1.45, 1 + 2 * off).toFixed(4)];
    }

    // Turntable motion: one rAF loop rotates every disc showing the current
    // record, accelerating on play and easing to a stop on pause.
    let angle = 0, vel = 0, last = performance.now(), spinEls = [];
    const reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    function collectSpin() {
      spinEls = S.currentId ? [...document.querySelectorAll(`.disc[data-track="${CSS.escape(S.currentId)}"] .disc-rot`)] : [];
    }
    function frame(now) {
      const dt = Math.min(0.1, (now - last) / 1000); last = now;
      const target = !audio.paused && !reduceMotion ? S.rpm * 6 * (audio.playbackRate || 1) : 0;
      vel += (target - vel) * Math.min(1, dt * (target > vel ? 1.8 : 1.2));
      if (Math.abs(vel) < 0.05 && target === 0) vel = 0;
      angle = (angle + vel * dt) % 360;
      for (const e of spinEls) e.style.transform = `rotate(${angle}deg)`;
      drawWave(now);
      requestAnimationFrame(frame);
    }

    // --- sidebar ------------------------------------------------------------------------
    const NAV = [
      ["library", [["all", "nAll"], ["radio", "nRadio"], ["liked", "nLiked"], ["recent", "nRecent"]]],
      ["collections", [["kazakh", "nStar"], ["languages", "nLang"], ["spoken", "nSpoken"], ["gallery", "nGallery"]]],
    ];
    function countFor(view) {
      switch (view) {
        case "all": case "radio": return playable().length;
        case "liked": return playable().filter((i) => S.liked.has(i.id)).length;
        case "recent": return S.recent.filter((id) => get(id)).length;
        case "kazakh": return playable().filter(isCentralAsian).length;
        case "languages": return new Set(playable().map((i) => i.language)).size;
        case "spoken": return playable().filter((i) => i.branch === "RADIO_SPEECH").length;
        case "gallery": return S.items.filter(isDoc).length;
        default: return "";
      }
    }
    function plCover(pl, big) {
      const imgs = pl.tracks.map(get).filter((i) => i && i.labels && i.labels.length).slice(0, 4);
      const c = el("div", { class: "pl-cover" + (imgs.length && imgs.length < 4 ? " single" : "") });
      if (!imgs.length) c.append(el("span", { class: "glyph", style: big ? "font-size:64px" : "font-size:14px" }, "♫"));
      else (imgs.length < 4 ? imgs.slice(0, 1) : imgs).forEach((i) => c.append(el("img", { src: adapter.labelUrl(i, 0), alt: "", loading: "lazy" })));
      return c;
    }
    function renderSide() {
      const markSvg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      markSvg.setAttribute("class", "pf-brand-mark"); markSvg.setAttribute("viewBox", "0 0 40 40"); markSvg.setAttribute("aria-hidden", "true");
      const kids = [el("div", { class: "pf-brand" }, markSvg,
        el("div", {}, el("div", { class: "pf-brand-name" }, "Patefon"),
          el("div", { class: "pf-brand-cyr" }, "АРХИВ ГРАМЗАПИСИ"),
          el("div", { class: "pf-brand-sub" }, t("brandSub"))))];
      for (const [group, entries] of NAV) {
        kids.push(el("div", { class: "pf-nav-group" }, el("div", { class: "pf-nav-title" }, t(group)),
          entries.map(([v, glyph]) => el("button", {
            class: "pf-nav-item", "aria-current": S.view === v ? "page" : null,
            onclick: () => (v === "radio" ? startRadio() : go(v)),
          }, el("span", { class: "ico", "aria-hidden": "true" }, icon(glyph)), el("span", { class: "label" }, t(v)),
            el("span", { class: "count pf-stamp-n" }, "№ " + countFor(v))))));
      }
      kids.push(el("div", { class: "pf-nav-group" },
        el("div", { class: "pf-nav-title" }, t("playlists"),
          el("button", { class: "pf-icon-btn", title: t("newPlaylist"), "aria-label": t("newPlaylist"), onclick: () => newPlaylist() }, icon("plus"))),
        S.playlists.map((pl) => el("button", {
          class: "pf-nav-item", "aria-current": S.view === "playlist" && S.arg === pl.id ? "page" : null, onclick: () => go("playlist", pl.id),
        }, plCover(pl), el("span", { class: "label" }, pl.name), el("span", { class: "count" }, pl.tracks.length))),
        el("button", { class: "pf-nav-item", onclick: () => newPlaylist() },
          el("span", { class: "ico", "aria-hidden": "true" }, "+"), el("span", { class: "label" }, t("newPlaylist")))));
      kids.push(el("div", { class: "pf-nav-group", style: "margin-top:auto" },
        el("button", { class: "pf-nav-item", onclick: exportLibrary }, el("span", { class: "ico" }, "⇩"), el("span", { class: "label" }, t("backup"))),
        el("button", { class: "pf-nav-item", onclick: importLibrary }, el("span", { class: "ico" }, "⇧"), el("span", { class: "label" }, t("restore")))));
      side.replaceChildren(...kids);
      // brand mark: a tiny record
      const mark = side.querySelector(".pf-brand-mark");
      mark.innerHTML = '<circle cx="20" cy="20" r="19" fill="#0F0E0E" stroke="rgba(229,184,76,.6)"/>'
        + '<circle cx="20" cy="20" r="14.5" fill="none" stroke="rgba(229,184,76,.2)"/><circle cx="20" cy="20" r="11.5" fill="none" stroke="rgba(229,184,76,.14)"/>'
        + '<circle cx="20" cy="20" r="8.5" fill="#CC2929"/><circle cx="20" cy="20" r="7.4" fill="none" stroke="#E5B84C" stroke-width=".6"/>'
        + '<path d="M20 13.6l1.5 3.5 3.8.3-2.9 2.5.9 3.7L20 21.6l-3.3 2 .9-3.7-2.9-2.5 3.8-.3z" fill="#E5B84C"/>'
        + '<circle cx="20" cy="20" r="0.9" fill="#0F0E0E"/>';
    }

    // --- topbar ------------------------------------------------------------------------
    const searchInput = el("input", { type: "search", "aria-label": "Search", autocomplete: "off", spellcheck: "false" });
    searchInput.addEventListener("input", () => { S.q = searchInput.value; S.shown = 120; renderContent(); });
    function renderTopbar() {
      searchInput.placeholder = t("search");
      const seg = (opts, cur, onPick) => el("div", { class: "pf-seg", role: "group" },
        opts.map(([v, lab, title]) => el("button", { "aria-pressed": String(cur === v), title: title || lab, onclick: () => onPick(v) }, lab)));
      topbar.replaceChildren(
        el("button", { class: "pf-icon-btn pf-menu-btn", "aria-label": "Menu", onclick: () => side.classList.toggle("open") }, icon("menu")),
        el("label", { class: "pf-search" }, icon("search"), searchInput, el("span", { class: "pf-kbd", "aria-hidden": "true" }, "/")),
        el("div", { class: "spacer" }),
        seg([["grid", "▦", t("grid")], ["table", "☰", t("table")]], S.layout, (v) => { S.layout = v; store.set("layout", v); renderTopbar(); renderContent(); }),
        seg([["en", "EN"], ["ru", "RU"], ["kz", "KZ"]], S.ui, setUi),
        el("button", { class: "pf-btn ghost pf-ded-btn", onclick: () => openDedication() }, t("dedicationBtn")),
        slot);
    }
    function setUi(lang) {
      S.ui = lang; store.set("ui", lang);
      document.documentElement.lang = lang === "kz" ? "kk" : lang;
      S.items.forEach((i) => { delete i._hay; });
      renderAll();
      if (story.classList.contains("open") && S.focusedId) openStory(S.focusedId);
    }

    // --- views ----------------------------------------------------------------------------
    function go(view, arg = null) {
      S.view = view; S.arg = arg; S.shown = 120;
      store.set("view", view); store.set("viewArg", arg);
      side.classList.remove("open");
      renderSide(); renderHero(); renderContent();
      main.scrollTo({ top: 0 });
    }
    function viewList() {
      let list;
      switch (S.view) {
        case "liked": list = playable().filter((i) => S.liked.has(i.id)); break;
        case "recent": list = S.recent.map(get).filter((i) => i && isPlayable(i)); break;
        case "kazakh": list = playable().filter(isCentralAsian); break;
        case "spoken": list = playable().filter((i) => i.branch === "RADIO_SPEECH"); break;
        case "lang": list = playable().filter((i) => i.language === S.arg); break;
        case "playlist": { const pl = S.playlists.find((p) => p.id === S.arg); list = pl ? pl.tracks.map(get).filter(Boolean) : []; break; }
        case "radio": list = S.radio ? S.order.map(get).filter(Boolean) : playable(); break;
        default: list = playable();
      }
      list = list.filter(matchesQ);
      if (S.view === "recent" || S.view === "playlist" || (S.view === "radio" && S.radio)) return list;
      const key = S.sort === "year" ? (i) => (i.year || "9999") + norm(i.title)
        : S.sort === "artist" ? (i) => norm(i.artist || "￿") + norm(i.title)
        : S.sort === "recent" ? (i) => i.downloaded_at || "" : (i) => norm(i.title);
      const out = list.slice().sort((a, b) => (key(a) < key(b) ? -1 : key(a) > key(b) ? 1 : 0));
      return S.sort === "recent" ? out.reverse() : out;
    }

    const CYR_STAMP = { all: "ВСЕ ПЛАСТИНКИ", radio: "РАДИОЭФИР", liked: "ИЗБРАННОЕ", recent: "НЕДАВНЕЕ",
      kazakh: "КАЗАХСКАЯ ССР · ҚАЗАҚ МУЗЫКАСЫ", languages: "ЯЗЫКИ НАРОДОВ", spoken: "РЕЧЬ И ЭФИР",
      gallery: "АРХИВ ПЕЧАТИ", lang: "ЯЗЫК", playlist: "ПОДБОРКА" };
    const FOND = { all: 1, radio: 2, liked: 3, recent: 4, kazakh: 5, languages: 6, lang: 6, spoken: 7, gallery: 8, playlist: 9 };
    function renderHero() {
      const v = S.view;
      const pl = v === "playlist" ? S.playlists.find((p) => p.id === S.arg) : null;
      if (v === "playlist" && !pl) { go("all"); return; }
      const titles = { all: t("all"), radio: t("radio"), liked: t("liked"), recent: t("recent"), kazakh: t("kazakh"),
        languages: t("languages"), spoken: t("spoken"), gallery: t("gallery") };
      const subs = { all: t("heroAll"), radio: t("heroRadio"), liked: t("heroLiked"), recent: t("heroRecent"),
        kazakh: t("heroKazakh"), languages: t("heroLanguages"), spoken: t("heroSpoken"), gallery: t("heroGallery") };
      const title = pl ? pl.name : v === "lang" ? langName(S.arg) : titles[v] || t("all");
      const sub = pl ? t("heroPlaylist") : v === "lang" ? (nativeName(S.arg) || "") : subs[v] || "";
      const list = v === "languages" || v === "gallery" ? [] : viewList();
      const count = v === "gallery" ? t("docs", S.items.filter(isDoc).length)
        : v === "languages" ? "" : t("tracks", list.length);
      const art = pl ? el("div", { class: "pf-hero-art" }, plCover(pl, true))
        : (v === "radio" || v === "kazakh") && (list.length || S.currentId)
          ? el("div", { class: "pf-hero-art" }, disc(S.currentId ? get(S.currentId) : list[0], { eager: true })) : null;
      const actions = [];
      if (v === "radio") {
        actions.push(el("button", { class: "pf-btn primary", onclick: startRadio, disabled: !playable().length }, icon("play"), t("startRadio")));
      } else if (v !== "languages" && v !== "gallery") {
        actions.push(
          el("button", { class: "pf-btn primary", disabled: !list.length, onclick: () => playIds(list.map((i) => i.id), 0, false) }, icon("play"), t("playAll")),
          el("button", { class: "pf-btn", disabled: !list.length, onclick: () => playIds(list.map((i) => i.id), 0, true) }, icon("shuffle"), t("shuffleAll")));
      }
      if (pl) {
        actions.push(
          el("button", { class: "pf-btn ghost", onclick: () => renamePlaylist(pl) }, t("rename")),
          el("button", { class: "pf-btn ghost", onclick: () => exportLibrary(pl) }, t("exportPl")),
          el("button", { class: "pf-btn ghost", onclick: () => deletePlaylist(pl) }, t("deletePl")));
      }
      hero.classList.toggle("pf-hero-kz", v === "kazakh");
      hero.replaceChildren(...[art, el("div", { style: "min-width:0" },
        el("div", { class: "pf-filing" },
          el("span", { class: "pf-stamp" }, CYR_STAMP[v] || (pl ? "ПОДБОРКА" : "АРХИВ")),
          el("span", { class: "mono" }, `ФОНД № ${FOND[v] || 9} · ДЕЛО № ${v === "languages" ? countFor("languages") : v === "gallery" ? S.items.filter(isDoc).length : list.length}`)),
        el("div", { class: "pf-eyebrow" }, [count, adapter.generatedAt && v === "all" ? t("generated", adapter.generatedAt) : null].filter(Boolean).join("  ·  ")),
        el("h1", {}, title), sub ? el("p", {}, sub) : null,
        actions.length ? el("div", { class: "pf-hero-actions" }, actions) : null)].filter(Boolean));
    }

    function empty(title, body, withDisc = true) {
      return el("div", { class: "pf-empty" }, el("div", {},
        withDisc ? disc(null) : null, el("div", { class: "pf-ornament", "aria-hidden": "true" }, "◆ ◆ ◆"),
        title ? el("h3", {}, title) : null, el("p", {}, body)));
    }

    function renderContent() {
      const v = S.view;
      if (v === "languages") return renderLanguages();
      if (v === "gallery") return renderGallery();
      const list = viewList();
      if (!list.length) {
        const msg = S.q ? empty(null, t("emptySearch"))
          : v === "kazakh" ? empty(t("kzWelcomeTitle"), t("kzWelcome"))
          : v === "liked" ? empty(null, t("emptyLiked"))
          : v === "recent" ? empty(null, t("emptyRecent"))
          : v === "spoken" ? empty(null, t("emptySpoken"))
          : v === "playlist" ? empty(null, t("emptyPlaylist")) : empty(null, t("emptyAll"));
        put(content, msg);
        collectSpin();
        return;
      }
      const sortable = !["recent", "playlist"].includes(v) && !(v === "radio" && S.radio);
      const sortSel = sortable ? el("select", {
        class: "pf-input", style: "width:auto;height:34px", "aria-label": "Sort",
        onchange: (e) => { S.sort = e.target.value; store.set("sort", S.sort); renderContent(); },
      }, [["title", "A-Z"], ["year", "1890 → 1960"], ["artist", "Artist"], ["recent", "Newest in archive"]].map(([val, lab]) =>
        el("option", { value: val, selected: S.sort === val ? "" : null }, lab))) : null;
      const ids = list.map((i) => i.id);
      const shown = list.slice(0, S.shown);
      const body = S.layout === "table" ? trackTable(shown, ids) : el("div", { class: "pf-grid" }, shown.map((it, i) => trackCard(it, ids, i)));
      put(content, 
        el("div", { class: "pf-section-head" }, el("span", { class: "pf-faint mono" }, t("tracks", list.length)), sortSel),
        body,
        list.length > S.shown ? el("div", { class: "pf-more" }, el("button", { class: "pf-btn", onclick: () => { S.shown += 120; renderContent(); } }, "+ " + Math.min(120, list.length - S.shown))) : null);
      collectSpin();
    }

    function langPill(it) {
      const cls = it.language === "Instrumental" ? "pf-pill quiet" : isCentralAsian(it) ? "pf-pill carmine" : "pf-pill";
      return el("span", { class: cls, title: it.language }, langName(it.language));
    }
    function heartBtn(it, extraClass = "") {
      const on = S.liked.has(it.id);
      return el("button", {
        class: "pf-icon-btn pf-heart " + extraClass, "aria-pressed": String(on), "data-heart": it.id,
        "aria-label": on ? t("unlike") : t("like"), title: on ? t("unlike") : t("like"),
        onclick: (e) => { e.stopPropagation(); toggleLike(it.id, e.currentTarget); },
      }, icon(on ? "heartFill" : "heart"));
    }
    function moreBtn(it) {
      return el("button", { class: "pf-icon-btn", "aria-label": t("addToPlaylist"), title: t("addToPlaylist"),
        onclick: (e) => { e.stopPropagation(); trackMenu(it, e.currentTarget); } }, icon("plus"));
    }
    function trackCard(it, ids, i) {
      const cur = S.currentId === it.id;
      return el("article", { class: "pf-card" + (cur ? " is-current" : "") },
        el("div", { class: "pf-card-art", title: t("openStory"), onclick: () => openStory(it.id) },
          disc(it),
          el("button", { class: "pf-card-play", "aria-label": `${cur && !audio.paused ? t("pause") : t("play")}: ${it.title}`,
            onclick: (e) => { e.stopPropagation(); cur ? togglePlay() : playIds(ids, i, false); } },
            icon(cur && !audio.paused ? "pause" : "play"))),
        el("div", { class: "pf-card-tools" }, heartBtn(it), moreBtn(it)),
        el("h3", { class: "pf-card-title", title: it.title }, it.title || "-"),
        el("div", { class: "pf-card-artist" }, it.artist || " "),
        el("div", { class: "pf-card-meta" }, langPill(it), it.year ? el("span", { class: "pf-year" }, it.year) : null));
    }
    function trackTable(list, ids) {
      return el("table", { class: "pf-table" },
        el("thead", {}, el("tr", {}, el("th", { class: "c-num" }, "#"), el("th", {}, t("colTitle")), el("th", { class: "c-lang" }, t("colLang")),
          el("th", { class: "c-cat" }, t("collection")), el("th", { class: "c-year" }, t("colYear")), el("th", { class: "c-tools" }, ""))),
        el("tbody", {}, list.map((it, i) => {
          const cur = S.currentId === it.id;
          return el("tr", { class: "row" + (cur ? " is-current" : ""), ondblclick: () => playIds(ids, i, false) },
            el("td", { class: "c-num" }, el("span", { class: "pf-row-num" }, cur && !audio.paused ? "♫" : i + 1),
              el("button", { class: "pf-icon-btn pf-row-num-play", "aria-label": t("play"), onclick: () => (cur ? togglePlay() : playIds(ids, i, false)) },
                icon(cur && !audio.paused ? "pause" : "play"))),
            el("td", {}, el("div", { class: "pf-row-title" },
              el("button", { style: "border:0;background:none;padding:0", "aria-label": t("openStory"), onclick: () => openStory(it.id) }, disc(it)),
              el("div", { class: "t" }, el("div", { class: "t-title", title: it.title }, it.title), el("div", { class: "t-artist" }, it.artist || "")))),
            el("td", { class: "c-lang" }, langPill(it)),
            el("td", { class: "c-cat" }, el("div", { class: "pf-ell", title: it.category_path }, it.category)),
            el("td", { class: "c-year" }, el("span", { class: "pf-year" }, it.year || "-")),
            el("td", { class: "c-tools" }, heartBtn(it), moreBtn(it)));
        })));
    }

    function renderLanguages() {
      const counts = new Map();
      for (const it of playable()) counts.set(it.language, (counts.get(it.language) || 0) + 1);
      let entries = [...counts.entries()].sort((a, b) => b[1] - a[1]);
      if (S.q) entries = entries.filter(([l]) => norm(l + " " + langName(l) + " " + nativeName(l)).includes(norm(S.q)));
      put(content, entries.length ? el("div", { class: "pf-lang-grid" }, entries.map(([l, n]) =>
        el("button", { class: "pf-lang-card", onclick: () => go("lang", l) },
          el("div", {}, el("div", { class: "name" }, langName(l)), el("div", { class: "native" }, nativeName(l))),
          el("div", { class: "n" }, t("tracks", n)))))
        : empty(null, S.q ? t("emptySearch") : t("emptyAll")));
    }

    function renderGallery() {
      const docs = S.items.filter(isDoc).filter(matchesQ)
        .sort((a, b) => (a.branch === b.branch ? norm(a.title).localeCompare(norm(b.title)) : a.branch === "PRINT" ? -1 : 1));
      if (!docs.length) { put(content, empty(null, S.q ? t("emptySearch") : t("emptyGallery"), false)); return; }
      put(content, el("div", { class: "pf-gallery" }, docs.slice(0, S.shown).map((d) =>
        el("button", { class: "pf-doc", onclick: () => openReader(d.id) },
          el("div", { class: "pf-doc-img" }, el("img", { src: adapter.mediaUrl(d), alt: "", loading: "lazy" })),
          el("div", { class: "pf-doc-body" },
            el("div", { class: "pf-doc-title" }, d.title),
            el("div", { class: "pf-card-meta" }, d.pdf ? el("span", { class: "pf-pill carmine" }, "PDF") : null,
              el("span", { class: "pf-pill quiet" }, langName(d.language)), d.year ? el("span", { class: "pf-year" }, d.year) : null))))),
      docs.length > S.shown ? el("div", { class: "pf-more" }, el("button", { class: "pf-btn", onclick: () => { S.shown += 120; renderGallery(); } }, "+")) : null);
    }

    // --- player bar ---------------------------------------------------------------------
    const npDiscBtn = el("button", { class: "pf-np-disc", "aria-label": t("openStory"), onclick: () => S.currentId && openStory(S.currentId) });
    const npTitle = el("div", { class: "pf-np-title" }), npArtist = el("div", { class: "pf-np-artist" });
    const npMeta = el("div", { class: "pf-np-meta" }), npHeartSlot = el("span");
    const btnShuffle = el("button", { class: "pf-icon-btn", onclick: () => toggleShuffle() }, icon("shuffle"));
    const btnPrev = el("button", { class: "pf-icon-btn", onclick: () => prev() }, icon("prev"));
    const btnPlay = el("button", { class: "pf-play", onclick: () => togglePlay() });
    const btnNext = el("button", { class: "pf-icon-btn", onclick: () => next(false) }, icon("next"));
    const btnRepeat = el("button", { class: "pf-icon-btn", onclick: () => cycleRepeat() }, icon("repeat"));
    const btnSurprise = el("button", { class: "pf-icon-btn pf-surprise", onclick: () => surprise() }, "🎲");
    const tCur = el("span", { class: "pf-time" }, "0:00"), tDur = el("span", { class: "pf-time" }, "0:00");
    const waveCanvas = el("canvas");
    const waveTip = el("span", { class: "pf-wave-tip" });
    const wave = el("div", { class: "pf-wave", role: "slider", tabindex: "0", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": "0" }, waveCanvas, waveTip);
    const speedSeg = el("div", { class: "pf-speed", role: "group" });
    const btnQueue = el("button", { class: "pf-icon-btn", onclick: () => toggleDrawer(queueDrawer, renderQueue) }, icon("queue"));
    const magicEye = el("span", { class: "pf-eye", title: "6Е5С", role: "img", "aria-label": "Tuning indicator 6Е5С" },
      el("span", { class: "pf-eye-glass" }), el("span", { class: "pf-eye-cap" }));
    const btnMute = el("button", { class: "pf-icon-btn", onclick: () => toggleMute() });
    const vol = el("input", { class: "pf-range", type: "range", min: "0", max: "1", step: "0.01", value: String(S.volume) });
    vol.addEventListener("input", () => {
      S.volume = Number(vol.value); audio.volume = S.volume; S.muted = S.volume === 0; audio.muted = S.muted;
      store.set("volume", S.volume); renderVolume();
    });
    player.replaceChildren(
      el("div", { class: "pf-np" }, npDiscBtn, el("div", { class: "pf-np-text" }, npTitle, npArtist, npMeta), npHeartSlot),
      el("div", { class: "pf-center" },
        el("div", { class: "pf-transport" }, btnShuffle, btnPrev, btnPlay, btnNext, btnRepeat, btnSurprise),
        el("div", { class: "pf-scrub" }, tCur, wave, tDur)),
      el("div", { class: "pf-right" }, magicEye, speedSeg, btnQueue, el("div", { class: "pf-vol" }, btnMute, vol)));

    function renderPlayerLabels() {
      const set = (b, label, extra) => { b.setAttribute("aria-label", label); b.title = label + (extra ? `  (${extra})` : ""); };
      set(btnShuffle, t("shuffle"), "S"); btnShuffle.setAttribute("aria-pressed", String(S.shuffle));
      set(btnPrev, t("prev"), "P"); set(btnNext, t("next"), "N");
      set(btnRepeat, S.repeat === "one" ? t("repeatOne") : S.repeat === "all" ? t("repeatAll") : t("repeatOff"), "R");
      btnRepeat.setAttribute("aria-pressed", String(S.repeat !== "off"));
      btnRepeat.classList.toggle("pf-repeat-one", S.repeat === "one");
      set(btnSurprise, t("surprise")); set(btnQueue, t("queueTitle"));
      set(btnPlay, audio.paused ? t("play") : t("pause"), "Space");
      btnPlay.replaceChildren(icon(audio.paused ? "play" : "pause"));
      vol.setAttribute("aria-label", t("volume"));
      wave.setAttribute("aria-label", t("nowPlaying"));
      speedSeg.setAttribute("aria-label", t("speed"));
      speedSeg.replaceChildren(...[0.85, 1, 1.15].map((r) => el("button", {
        "aria-pressed": String(S.speed === r), title: t("speed"),
        onclick: () => { S.speed = r; store.set("speed", r); audio.playbackRate = r; renderPlayerLabels(); },
      }, r === 1 ? "1.0×" : r + "×")));
      renderVolume();
    }
    function renderVolume() {
      const v = S.muted ? 0 : S.volume;
      vol.style.setProperty("--pct", `${Math.round(v * 100)}%`);
      btnMute.replaceChildren(icon(v === 0 ? "mute" : "volume"));
      const lab = v === 0 ? t("unmute") : t("mute");
      btnMute.setAttribute("aria-label", lab); btnMute.title = lab + "  (M)";
    }
    function renderNowPlaying() {
      const it = S.currentId ? get(S.currentId) : null;
      npDiscBtn.replaceChildren(disc(it, { eager: true }));
      npTitle.textContent = it ? it.title : t("choose");
      npArtist.textContent = it ? it.artist || "" : t("shortcuts");
      npMeta.replaceChildren(...(it ? [it.year ? el("span", { class: "pf-pill quiet mono" }, it.year) : null, langPill(it)] : []).filter(Boolean));
      npHeartSlot.replaceChildren(it ? heartBtn(it) : "");
      document.title = it ? `${it.title} · Patefon` : "Patefon";
      collectSpin();
    }

    // Waveform scrubber + visualizer.
    let analyser = null, freq = null, audioCtx = null, webAudioTried = false;
    function ensureWebAudio() {
      // file:// media is cross-origin to Web Audio and would be silenced, so the
      // visualizer only taps the stream when served over http(s).
      if (webAudioTried || !/^https?:$/.test(location.protocol)) return;
      webAudioTried = true;
      try {
        audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        const src = audioCtx.createMediaElementSource(audio);
        analyser = audioCtx.createAnalyser(); analyser.fftSize = 256; analyser.smoothingTimeConstant = 0.78;
        src.connect(analyser); analyser.connect(audioCtx.destination);
        freq = new Uint8Array(analyser.frequencyBinCount);
      } catch { analyser = null; }
    }
    let waveSeed = [], waveSeedFor = null;
    function seedWave(id, n) {
      if (waveSeedFor === id + ":" + n) return;
      let h = hash(id || "none"); waveSeed = [];
      for (let i = 0; i < n; i++) {
        h = Math.imul(h ^ (h >>> 13), 1274126177) >>> 0;
        const env = 0.55 + 0.45 * Math.sin((i / n) * Math.PI);
        waveSeed.push(0.25 + 0.75 * env * ((h % 1000) / 1000));
      }
      waveSeedFor = id + ":" + n;
    }
    let hoverX = null;
    let eyeLevel = 0;
    function drawEye(now) {
      let target = 0;
      if (!audio.paused) {
        if (analyser && freq) {
          let sum = 0;
          for (let i = 0; i < freq.length; i++) sum += freq[i];
          target = Math.min(1, (sum / freq.length / 255) * 2.2);
        } else {
          target = 0.55 + 0.25 * Math.sin(now / 520) * Math.sin(now / 230);
        }
      }
      eyeLevel += (target - eyeLevel) * 0.12;
      magicEye.style.setProperty("--eye", eyeLevel.toFixed(3));
      magicEye.classList.toggle("on", !audio.paused);
    }
    function drawWave(now) {
      drawEye(now);
      const w = wave.clientWidth, h = wave.clientHeight;
      if (!w) return;
      const dpr = window.devicePixelRatio || 1;
      if (waveCanvas.width !== Math.round(w * dpr) || waveCanvas.height !== Math.round(h * dpr)) {
        waveCanvas.width = Math.round(w * dpr); waveCanvas.height = Math.round(h * dpr);
      }
      const g = waveCanvas.getContext("2d");
      g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h);
      const bars = Math.max(24, Math.floor(w / 4.5));
      seedWave(S.currentId, bars);
      const pct = audio.duration ? audio.currentTime / audio.duration : 0;
      const playing = !audio.paused;
      if (analyser && playing) analyser.getByteFrequencyData(freq);
      const bw = w / bars;
      for (let i = 0; i < bars; i++) {
        let amp = waveSeed[i];
        if (playing) {
          if (analyser) {
            const bin = freq[Math.floor((i / bars) * freq.length * 0.7)] / 255;
            amp = Math.max(0.12, amp * 0.45 + bin * 0.75);
          } else if (!reduceMotion) {
            amp *= 0.82 + 0.18 * Math.sin(now / 240 + i * 0.7) * Math.sin(now / 410 + i * 0.31);
          }
        }
        const bh = Math.max(2, amp * (h - 4));
        const x = i * bw;
        const played = (i + 0.5) / bars <= pct;
        const hovered = hoverX != null && x <= hoverX;
        g.fillStyle = played ? "#D4A853" : hovered ? "rgba(212,168,83,0.42)" : "rgba(166,151,133,0.32)";
        g.fillRect(x + 0.6, (h - bh) / 2, Math.max(1, bw - 1.6), bh);
      }
    }
    function seekFromEvent(e) {
      const r = wave.getBoundingClientRect();
      const p = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
      if (audio.duration) audio.currentTime = p * audio.duration;
    }
    wave.addEventListener("pointermove", (e) => {
      const r = wave.getBoundingClientRect();
      hoverX = e.clientX - r.left;
      const p = Math.min(1, Math.max(0, hoverX / r.width));
      waveTip.textContent = fmtTime(p * (audio.duration || 0));
      waveTip.style.left = `${p * 100}%`;
      if (e.buttons === 1) seekFromEvent(e);
    });
    wave.addEventListener("pointerleave", () => { hoverX = null; });
    wave.addEventListener("pointerdown", (e) => { wave.setPointerCapture(e.pointerId); seekFromEvent(e); });
    wave.addEventListener("keydown", (e) => {
      if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        e.preventDefault(); e.stopPropagation();
        audio.currentTime = Math.max(0, Math.min(audio.duration || 0, audio.currentTime + (e.key === "ArrowRight" ? 5 : -5)));
      }
    });

    // --- playback engine -------------------------------------------------------------------
    function playIds(ids, index = 0, shuffle = false) {
      if (!ids.length) return;
      S.radio = false;
      S.base = ids.slice();
      if (shuffle) {
        const first = ids[index] && index ? ids[index] : ids[Math.floor(Math.random() * ids.length)];
        S.order = [first, ...shuffled(ids.filter((x) => x !== first))];
        S.pos = 0;
        if (!S.shuffle) { S.shuffle = true; store.set("shuffle", true); }
      } else if (S.shuffle) {
        const first = ids[index];
        S.order = [first, ...shuffled(ids.filter((x) => x !== first))]; S.pos = 0;
      } else { S.order = ids.slice(); S.pos = index; }
      load(S.order[S.pos], true);
    }
    function startRadio() {
      const ids = playable().map((i) => i.id);
      if (!ids.length) { go("radio"); return; }
      S.radio = true; S.base = ids; S.order = shuffled(ids); S.pos = 0;
      if (S.view !== "radio") { S.view = "radio"; store.set("view", "radio"); }
      load(S.order[0], true);
      renderSide(); renderHero(); renderContent();
    }
    function surprise() {
      const ids = playable().map((i) => i.id).filter((x) => x !== S.currentId);
      if (!ids.length) return;
      const pick = ids[Math.floor(Math.random() * ids.length)];
      // Keep whatever was queued: the surprise plays now, the queue resumes after it.
      if (S.pos < 0) { S.order = [pick]; S.pos = 0; } else { S.order.splice(S.pos + 1, 0, pick); S.pos += 1; }
      load(pick, true);
    }
    function load(id, autoplay) {
      const it = get(id);
      if (!it) return;
      S.currentId = id;
      audio.src = adapter.mediaUrl(it);
      audio.playbackRate = S.speed;
      if (autoplay) {
        ensureWebAudio();
        if (audioCtx && audioCtx.state === "suspended") audioCtx.resume();
        audio.play().catch(() => { /* autoplay blocked until the next click */ });
      }
      S.recent = [id, ...S.recent.filter((x) => x !== id)].slice(0, 150); saveLib();
      updateMediaSession(it);
      renderNowPlaying(); refreshCurrentMarks();
      if (queueDrawer.classList.contains("open")) renderQueue();
      if (S.view === "recent") renderContent();
      countRecentInSide();
    }
    function togglePlay() {
      if (!S.currentId) {
        const list = viewList();
        if (list.length) playIds(list.map((i) => i.id), 0, false); else startRadio();
        return;
      }
      if (audio.paused) {
        ensureWebAudio();
        if (audioCtx && audioCtx.state === "suspended") audioCtx.resume();
        audio.play().catch(() => {});
      } else audio.pause();
    }
    function next(auto) {
      if (!S.order.length) return;
      if (auto && S.repeat === "one") { audio.currentTime = 0; audio.play().catch(() => {}); return; }
      if (S.pos + 1 < S.order.length) { S.pos += 1; load(S.order[S.pos], true); return; }
      if (S.radio) {
        const ids = playable().map((i) => i.id);
        const fresh = shuffled(ids.filter((x) => x !== S.currentId));
        S.order = S.order.concat(fresh.length ? fresh : ids); S.pos += 1;
        load(S.order[S.pos], true); return;
      }
      if (S.repeat === "all") { S.pos = 0; load(S.order[0], true); return; }
      if (!auto) { S.pos = 0; load(S.order[0], false); }
    }
    function prev() {
      if (audio.currentTime > 3 || S.pos <= 0) { audio.currentTime = 0; return; }
      S.pos -= 1; load(S.order[S.pos], true);
    }
    function toggleShuffle() {
      S.shuffle = !S.shuffle; store.set("shuffle", S.shuffle);
      if (S.order.length && S.pos >= 0) {
        const done = S.order.slice(0, S.pos + 1);
        if (S.shuffle) S.order = done.concat(shuffled(S.order.slice(S.pos + 1)));
        else if (S.base.length) {
          const at = S.base.indexOf(S.currentId);
          S.order = at >= 0 ? S.base.slice() : done.concat(S.order.slice(S.pos + 1));
          S.pos = at >= 0 ? at : S.pos;
        }
      }
      renderPlayerLabels();
      if (queueDrawer.classList.contains("open")) renderQueue();
      toast(t("shuffle") + (S.shuffle ? " ✓" : " ✕"));
    }
    function cycleRepeat() {
      S.repeat = S.repeat === "off" ? "all" : S.repeat === "all" ? "one" : "off";
      store.set("repeat", S.repeat); renderPlayerLabels();
      toast(S.repeat === "one" ? t("repeatOne") : S.repeat === "all" ? t("repeatAll") : t("repeatOff"));
    }
    function toggleMute() {
      if (S.muted || S.volume === 0) { S.muted = false; if (S.volume === 0) S.volume = 0.6; }
      else S.muted = true;
      audio.muted = S.muted; audio.volume = S.volume; vol.value = String(S.muted ? 0 : S.volume);
      renderVolume();
    }
    function addToQueue(id) {
      if (S.pos < 0) { S.order = [id]; S.pos = 0; load(id, true); return; }
      S.order.push(id); toast(t("queued"));
      if (queueDrawer.classList.contains("open")) renderQueue();
    }
    function playNext(id) {
      if (S.pos < 0) { addToQueue(id); return; }
      S.order.splice(S.pos + 1, 0, id); toast(t("playingNext"));
      if (queueDrawer.classList.contains("open")) renderQueue();
    }
    audio.addEventListener("play", () => { S.errors = 0; renderPlayerLabels(); refreshCurrentMarks(); setMediaState(); });
    audio.addEventListener("pause", () => { renderPlayerLabels(); refreshCurrentMarks(); setMediaState(); });
    audio.addEventListener("ended", () => next(true));
    audio.addEventListener("timeupdate", () => {
      tCur.textContent = fmtTime(audio.currentTime);
      wave.setAttribute("aria-valuenow", String(audio.duration ? Math.round((audio.currentTime / audio.duration) * 100) : 0));
      wave.setAttribute("aria-valuetext", `${fmtTime(audio.currentTime)} / ${fmtTime(audio.duration)}`);
    });
    audio.addEventListener("loadedmetadata", () => {
      tDur.textContent = fmtTime(audio.duration);
      if (story.classList.contains("open") && S.focusedId === S.currentId) fillFormat(get(S.currentId));
    });
    audio.addEventListener("error", () => {
      if (!S.currentId) return;
      S.errors += 1; toast(t("cantPlay"));
      if (S.errors < 3 && S.pos + 1 < S.order.length) setTimeout(() => next(false), 1200);
    });
    function refreshCurrentMarks() {
      // Update play/pause glyphs and highlight without re-rendering the list.
      document.querySelectorAll(".pf-card.is-current, .pf-table tr.is-current").forEach((n) => n.classList.remove("is-current"));
      if (S.view !== "languages" && S.view !== "gallery") {
        const scrollTop = main.scrollTop;
        renderContent();
        main.scrollTop = scrollTop;
      }
      collectSpin();
    }

    // Media Session: lock screen / hardware keys.
    function updateMediaSession(it) {
      if (!("mediaSession" in navigator)) return;
      try {
        navigator.mediaSession.metadata = new MediaMetadata({
          title: it.title, artist: it.artist || "", album: it.category || "Patefon",
          artwork: it.labels && it.labels.length ? [{ src: new URL(adapter.labelUrl(it, 0), location.href).href }] : [],
        });
        navigator.mediaSession.setActionHandler("play", () => togglePlay());
        navigator.mediaSession.setActionHandler("pause", () => audio.pause());
        navigator.mediaSession.setActionHandler("nexttrack", () => next(false));
        navigator.mediaSession.setActionHandler("previoustrack", () => prev());
      } catch { /* optional */ }
    }
    function setMediaState() {
      if ("mediaSession" in navigator) navigator.mediaSession.playbackState = audio.paused ? "paused" : "playing";
    }

    // --- likes, playlists -------------------------------------------------------------------
    function toggleLike(id, btn) {
      if (S.liked.has(id)) { S.liked.delete(id); rememberGone("liked", id); } else S.liked.add(id);
      saveLib();
      const on = S.liked.has(id);
      document.querySelectorAll(`[data-heart="${CSS.escape(id)}"]`).forEach((b) => {
        b.setAttribute("aria-pressed", String(on)); b.replaceChildren(icon(on ? "heartFill" : "heart"));
        b.setAttribute("aria-label", on ? t("unlike") : t("like")); b.title = on ? t("unlike") : t("like");
      });
      if (btn && on) { btn.classList.remove("pop"); void btn.offsetWidth; btn.classList.add("pop"); }
      renderSide();
      if (S.view === "liked") { renderHero(); renderContent(); }
    }
    function dialog(title, value, okLabel) {
      return new Promise((resolve) => {
        const input = el("input", { class: "pf-input", value: value || "", maxlength: "80", "aria-label": t("plName") });
        const d = el("dialog", { class: "pf-dialog" },
          el("form", { method: "dialog", onsubmit: (e) => { e.preventDefault(); d.close(); resolve(input.value.trim() || null); } },
            el("h3", {}, title), input,
            el("div", { class: "pf-dialog-actions" },
              el("button", { type: "button", class: "pf-btn ghost", onclick: () => { d.close(); resolve(null); } }, t("cancel")),
              el("button", { type: "submit", class: "pf-btn primary" }, okLabel))));
        d.addEventListener("close", () => { d.remove(); resolve(null); });
        document.body.append(d); d.showModal(); input.select();
      });
    }
    async function newPlaylist(withTrack) {
      const name = await dialog(t("newPlaylist"), t("plDefault") + " " + (S.playlists.length + 1), t("create"));
      if (!name) return null;
      const pl = { id: uid(), name, tracks: withTrack ? [withTrack] : [], created: new Date().toISOString() };
      S.playlists.push(pl); saveLib(); renderSide();
      if (withTrack) toast(t("added", name)); else go("playlist", pl.id);
      return pl;
    }
    async function renamePlaylist(pl) {
      const name = await dialog(t("rename"), pl.name, t("save"));
      if (!name) return;
      pl.name = name; saveLib(); renderSide(); renderHero();
    }
    function deletePlaylist(pl) {
      if (!confirm(t("confirmDelete", pl.name))) return;
      S.playlists = S.playlists.filter((p) => p !== pl); rememberGone("playlist", pl.id); saveLib(); go("all");
    }
    function addToPlaylist(pl, id) {
      if (!pl.tracks.includes(id)) pl.tracks.push(id);
      saveLib(); renderSide(); toast(t("added", pl.name));
      if (S.view === "playlist" && S.arg === pl.id) { renderHero(); renderContent(); }
    }
    let popEl = null;
    function closePop() { if (popEl) { popEl.remove(); popEl = null; } }
    function trackMenu(it, anchor) {
      closePop();
      const items = [
        el("button", { onclick: () => { closePop(); playNext(it.id); } }, "⤴  " + t("playNext")),
        el("button", { onclick: () => { closePop(); addToQueue(it.id); } }, "≡  " + t("addToQueue")),
        el("button", { onclick: () => { closePop(); openStory(it.id); } }, "❦  " + t("story")),
        el("hr"), el("div", { class: "sub" }, t("addToPlaylist")),
        ...S.playlists.map((pl) => el("button", { onclick: () => { closePop(); addToPlaylist(pl, it.id); } }, "♫  " + pl.name)),
        el("button", { onclick: () => { closePop(); newPlaylist(it.id); } }, "+  " + t("newPlaylist")),
      ];
      if (S.view === "playlist") {
        const pl = S.playlists.find((p) => p.id === S.arg);
        if (pl && pl.tracks.includes(it.id)) items.push(el("hr"), el("button", { onclick: () => {
          closePop(); pl.tracks = pl.tracks.filter((x) => x !== it.id); rememberGone("track", it.id, pl.id);
          saveLib(); renderSide(); renderHero(); renderContent();
        } }, "✕  " + t("removeFromPlaylist")));
      }
      popEl = el("div", { class: "pf-pop", role: "menu" }, items);
      document.body.append(popEl);
      const r = anchor.getBoundingClientRect(), pw = popEl.offsetWidth, ph = popEl.offsetHeight;
      popEl.style.left = `${Math.max(8, Math.min(window.innerWidth - pw - 8, r.right - pw))}px`;
      popEl.style.top = `${r.bottom + ph + 8 > window.innerHeight ? Math.max(8, r.top - ph - 6) : r.bottom + 6}px`;
      const firstBtn = popEl.querySelector("button"); if (firstBtn) firstBtn.focus();
    }
    document.addEventListener("pointerdown", (e) => { if (popEl && !popEl.contains(e.target)) closePop(); });

    function download(name, obj) {
      const blob = new Blob([JSON.stringify(obj, null, 2)], { type: "application/json" });
      const a = el("a", { href: URL.createObjectURL(blob), download: name });
      document.body.append(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    }
    function exportLibrary(only) {
      const playlists = only && only.id ? [only] : S.playlists;
      const stamp = new Date().toISOString().slice(0, 10);
      download(only && only.id ? `patefon-${only.name.replace(/[^\wЀ-ӿ-]+/g, "_")}-${stamp}.json` : `patefon-library-${stamp}.json`, {
        app: "patefon", version: 1, exported_at: new Date().toISOString(),
        playlists, liked: only && only.id ? [] : [...S.liked], recent: only && only.id ? [] : S.recent,
      });
    }
    function importLibrary() {
      const input = el("input", { type: "file", accept: "application/json,.json" });
      input.addEventListener("change", async () => {
        try {
          const data = JSON.parse(await input.files[0].text());
          if (!data || data.app !== "patefon" || !Array.isArray(data.playlists)) throw new Error("bad");
          let n = 0;
          for (const pl of data.playlists) {
            if (!pl || typeof pl.name !== "string" || !Array.isArray(pl.tracks)) continue;
            const clean = { id: String(pl.id || uid()), name: pl.name.slice(0, 80), tracks: pl.tracks.map(String), created: pl.created || "" };
            const i = S.playlists.findIndex((p) => p.id === clean.id);
            if (i >= 0) S.playlists[i] = clean; else S.playlists.push(clean);
            n += 1;
          }
          (data.liked || []).forEach((id) => S.liked.add(String(id)));
          if (Array.isArray(data.recent) && !S.recent.length) S.recent = data.recent.map(String);
          saveLib(); renderAll(); toast(t("imported", n));
        } catch { toast(t("importFailed")); }
      });
      input.click();
    }

    // --- drawers ------------------------------------------------------------------------------
    function openDrawer(d) {
      [story, queueDrawer].forEach((x) => { if (x !== d) { x.classList.remove("open"); x.setAttribute("aria-hidden", "true"); } });
      d.classList.add("open"); d.setAttribute("aria-hidden", "false"); scrim.classList.add("open");
    }
    function closeAll() {
      [story, queueDrawer].forEach((x) => { x.classList.remove("open"); x.setAttribute("aria-hidden", "true"); });
      scrim.classList.remove("open"); side.classList.remove("open"); closePop();
      document.querySelectorAll(".pf-lightbox").forEach((n) => n.remove());
    }
    function toggleDrawer(d, render) {
      if (d.classList.contains("open")) { closeAll(); return; }
      render(); openDrawer(d);
    }
    function drawerHead(title, extra) {
      return el("div", { class: "pf-drawer-head" }, el("h2", {}, title), extra || null,
        el("button", { class: "pf-icon-btn", "aria-label": "Close", onclick: closeAll }, icon("close")));
    }
    function lightbox(src) {
      if (!src) return;
      const box = el("div", { class: "pf-lightbox", role: "dialog", "aria-label": "Zoom", onclick: () => box.remove() }, el("img", { src, alt: "" }));
      document.body.append(box);
    }

    function renderQueue() {
      const cur = S.currentId ? get(S.currentId) : null;
      const upcoming = S.order.slice(S.pos + 1).map((id, k) => ({ id, k: S.pos + 1 + k })).filter((x) => get(x.id));
      const row = (it, k, isNow) => el("div", { class: "pf-q-row" + (isNow ? " now" : "") },
        disc(it),
        el("div", { class: "t", onclick: () => { if (!isNow) { S.pos = k; load(it.id, true); } } },
          el("div", { class: "t-title" }, it.title), el("div", { class: "t-artist" }, it.artist || "")),
        isNow ? null : el("button", { class: "pf-icon-btn", "aria-label": t("remove"), title: t("remove"),
          onclick: () => { S.order.splice(k, 1); renderQueue(); } }, icon("close")));
      queueDrawer.replaceChildren(
        drawerHead(t("queueTitle"), upcoming.length ? el("button", { class: "pf-btn ghost", style: "height:32px;padding:0 12px",
          onclick: () => { S.order = S.order.slice(0, S.pos + 1); S.radio = false; renderQueue(); } }, t("clearQueue")) : null),
        el("div", { class: "pf-drawer-body" },
          cur ? [el("div", { class: "pf-q-label" }, t("nowPlaying")), row(cur, S.pos, true)] : null,
          el("div", { class: "pf-q-label" }, t("queueTitle") + (S.radio ? " · " + t("radio") : "")),
          upcoming.length ? upcoming.slice(0, 300).map((x) => row(get(x.id), x.k, false)) : el("p", { class: "pf-muted" }, t("queueEmpty"))));
      collectSpin();
    }

    // Parse "Key: value" lines the archive keeps in the notes.
    function splitNotes(text) {
      const facts = {}, free = [];
      let inFacts = false;
      for (const line of (text || "").split(/\n/)) {
        const m = line.match(/^\s*([^:]{2,40}):\s*(.+)$/);
        if (m && !/^https?$/i.test(m[1])) { facts[m[1].trim().toLowerCase()] = m[2].trim(); inFacts = true; }
        else if (!inFacts || line.trim()) free.push(line);
      }
      return { facts, free: free.join("\n").trim() };
    }
    let formatDd = null;
    async function fillFormat(it) {
      if (!formatDd || !it) return;
      const parts = [ext(it)];
      if (adapter.inspect) {
        try {
          const v = await adapter.inspect(it.id);
          if (v && v.bitrate) parts.push(`${Math.round(v.bitrate / 1000)} kbps`);
          if (v && v.length) parts.push(fmtTime(v.length));
        } catch { /* optional */ }
      } else if (S.currentId === it.id && audio.duration) parts.push(fmtTime(audio.duration));
      formatDd.textContent = parts.join(" · ");
    }

    // Lyrics & notes: only what the archive itself provides (the record's own
    // label text and liner notes) plus lyrics already stored for it
    // (public-domain Wikisource texts or files you added).
    function looksLikeVerse(text) {
      const lines = text.split("\n").map((l) => l.trim()).filter(Boolean);
      if (lines.length < 3) return false;
      return lines.reduce((s, l) => s + l.length, 0) / lines.length < 48;
    }
    function verseBlock(text) {
      return el("div", { class: "pf-verse" }, text.split(/\n{2,}/).map((stanza) =>
        el("p", {}, stanza.split("\n").map((ln, i) => [i ? el("br") : null, ln.trim()]))));
    }
    let lyricSync = null;   // {id, lines:[[t, el]], box}
    function syncLyrics() {
      if (!lyricSync || lyricSync.id !== S.currentId) return;
      const now = audio.currentTime;
      let active = -1;
      lyricSync.lines.forEach(([tt], i) => { if (tt <= now + 0.15) active = i; });
      if (active === lyricSync.active) return;
      lyricSync.active = active;
      lyricSync.lines.forEach(([, n], i) => n.classList.toggle("active", i === active));
      const n = active >= 0 && lyricSync.lines[active][1];
      if (n && story.classList.contains("open")) n.scrollIntoView({ block: "center", behavior: reduceMotion ? "auto" : "smooth" });
    }
    audio.addEventListener("timeupdate", syncLyrics);

    function notesPanel(d, facts, free) {
      const parts = [];
      const lyr = d.lyrics;
      lyricSync = null;
      if (lyr) {
        const src = lyr.source === "wikisource"
          ? el("span", { class: "pf-pill quiet" }, t("srcWikisource"),
              lyr.url ? [" · ", el("a", { href: lyr.url, target: "_blank", rel: "noopener noreferrer" }, "ru.wikisource.org")] : null)
          : el("span", { class: "pf-pill quiet" }, t("srcManual"));
        let body;
        if (lyr.kind === "lrc" && Array.isArray(lyr.lines) && lyr.lines.length) {
          const rows = lyr.lines.map(([tt, line]) => [tt, el("div", { class: "pf-lyric-line", role: "button", tabindex: "0",
            onclick: () => { if (S.currentId === d.id) audio.currentTime = tt; } }, line || "♪")]);
          body = el("div", { class: "pf-lyrics-synced" }, rows.map(([, n]) => n));
          lyricSync = { id: d.id, lines: rows, active: -2 };
        } else {
          body = verseBlock(lyr.text || "");
        }
        parts.push(el("section", { class: "pf-notes-sec" },
          el("div", { class: "pf-notes-head" }, el("h4", { class: "pf-notes-title" }, t("lyricsTitle")), src), body));
      }
      const own = d.notes || {};          // extracted by rr_archive/notes.py, line breaks intact
      const label = own.label || free;
      if (label) {
        parts.push(el("section", { class: "pf-notes-sec" }, el("h4", { class: "pf-notes-title" }, t("onLabel")),
          looksLikeVerse(label) ? verseBlock(label) : el("p", { class: "pf-liner" }, label)));
      }
      const liner = own.liner || facts["additional information"];
      if (liner) {
        parts.push(el("section", { class: "pf-notes-sec" }, el("h4", { class: "pf-notes-title" }, t("linerNotes")),
          looksLikeVerse(liner) ? verseBlock(liner) : el("p", { class: "pf-liner pf-dropcap" }, liner)));
      }
      if (!parts.length) {
        parts.push(el("div", { class: "pf-silent" }, el("div", { class: "pf-ornament", "aria-hidden": "true" }, "◆ ◆ ◆"),
          el("p", {}, t("silentNotes"))));
      }
      return el("div", { class: "pf-notes-panel" }, parts);
    }

    async function openStory(id) {
      const base = get(id);
      if (!base) return;
      S.focusedId = id;
      window.dispatchEvent(new CustomEvent("patefon:focus", { detail: { id } }));
      let d = base;
      try { d = { ...base, ...(await adapter.detail(id)) }; } catch { /* list data is enough */ }
      const { facts, free } = splitNotes(d.description);
      const f = (...keys) => keys.map((k) => facts[k]).find(Boolean);
      const recorded = [d.year_raw || d.year, f("recording place")].filter(Boolean).join(" · ");
      formatDd = el("dd", { class: "mono" }, ext(d));
      const rows = [
        [t("catalog"), d.catalog_number, true], [t("matrix"), d.matrix_number, true], [t("recorded"), recorded],
        [t("composer"), d.composer || f("composer")], [t("work"), (d.notes && d.notes.work) || f("larger composition name")],
        [t("lyricist"), f("lyrics by")], [t("conductor"), f("bandmaster or conductor")],
        [t("accompaniment"), f("accompaniment type")], [t("label"), d.record_label], [t("collection"), d.category_path],
        [t("size"), f("record size")], [t("rpm"), f("speed of transfer")],
      ].filter(([, v]) => v);
      const isCur = S.currentId === id;
      const playBtn = isPlayable(d) ? el("button", { class: "pf-btn primary", onclick: () => {
        if (S.currentId === id) togglePlay(); else playIds([id], 0, false);
        setTimeout(() => openStory(id), 50);
      } }, icon(isCur && !audio.paused ? "pause" : "play"), isCur && !audio.paused ? t("pause") : t("play")) : null;
      const firstLabel = d.labels && d.labels.length ? adapter.labelUrl(d, 0) : null;
      const rpmSeg = el("div", { class: "pf-seg", role: "group", style: "margin:0 auto" },
        [[8, t("discSlow")], [33.333, t("disc33")], [78, t("disc78")]].map(([r, lab]) => el("button", { "aria-pressed": String(Math.round(S.rpm) === Math.round(r)),
          onclick: () => { S.rpm = r; store.set("spinRpm", r); openStory(id); } }, lab)));

      const recordPanel = el("div", { class: "pf-tabpanel", role: "tabpanel" },
        el("dl", { class: "pf-facts" }, rows.map(([k, v, mono]) => [el("dt", {}, k), el("dd", { class: mono ? "mono" : null }, v)]),
          isPlayable(d) ? [el("dt", {}, t("format")), formatDd] : null),
        d.source_url ? el("p", { style: "margin-top:20px" }, el("a", { href: d.source_url, target: "_blank", rel: "noopener noreferrer" }, t("source"), " ", icon("external"))) : null);
      const notes = notesPanel(d, facts, free);
      const notesPanelEl = el("div", { class: "pf-tabpanel", role: "tabpanel" }, notes);
      const tabBtn = (key, label) => el("button", { role: "tab", class: "pf-tab", "aria-selected": String(S.storyTab === key),
        onclick: () => { S.storyTab = key; store.set("storyTab", key); showTab(); } }, label);
      const tabRecord = tabBtn("record", t("tabRecord")), tabNotes = tabBtn("notes", t("tabNotes"));
      function showTab() {
        const notesOn = S.storyTab === "notes";
        tabRecord.setAttribute("aria-selected", String(!notesOn)); tabNotes.setAttribute("aria-selected", String(notesOn));
        recordPanel.hidden = notesOn; notesPanelEl.hidden = !notesOn;
        if (notesOn) { lyricSync && (lyricSync.active = -2); syncLyrics(); }
      }

      story.replaceChildren(
        drawerHead(t("story")),
        el("div", { class: "pf-drawer-body" },
          el("div", { class: "pf-story-disc", title: t("zoom"), onclick: () => lightbox(firstLabel) }, disc(d, { eager: true })),
          el("div", { style: "display:flex;justify-content:center;margin:-8px 0 18px" }, rpmSeg),
          el("h3", { class: "pf-story-title" }, d.title),
          d.artist ? el("p", { class: "pf-story-artist" }, d.artist) : null,
          el("div", { class: "pf-story-badges" }, langPill(d), d.year ? el("span", { class: "pf-pill quiet mono" }, d.year) : null,
            ext(d) ? el("span", { class: "pf-pill quiet mono" }, ext(d)) : null),
          el("div", { class: "pf-story-actions" }, playBtn, isPlayable(d) ? heartBtn(d) : null, isPlayable(d) ? moreBtn(d) : null,
            d.pdf && adapter.pdfUrl ? el("a", { class: "pf-btn", href: adapter.pdfUrl(d), target: "_blank", rel: "noopener" }, icon("book"), t("openPdf")) : null),
          el("div", { class: "pf-tabs", role: "tablist" }, tabRecord, tabNotes),
          recordPanel, notesPanelEl));
      showTab();
      story.classList.remove("wide");
      openDrawer(story); collectSpin();
      if (isPlayable(d)) fillFormat(d);
    }

    async function openReader(id) {
      const base = get(id);
      if (!base) return;
      S.focusedId = id;
      window.dispatchEvent(new CustomEvent("patefon:focus", { detail: { id } }));
      let d = base;
      try { d = { ...base, ...(await adapter.detail(id)) }; } catch { /* fine */ }
      const imgs = [adapter.mediaUrl(d), ...(d.labels || []).map((_, i) => adapter.labelUrl(d, i))];
      const frameSlot = el("div");
      const pdfUrl = d.pdf && adapter.pdfUrl ? adapter.pdfUrl(d) : null;
      const readBtn = pdfUrl ? el("button", { class: "pf-btn primary", onclick: () => {
        if (frameSlot.firstChild) { frameSlot.replaceChildren(); readBtn.lastChild.textContent = t("readHere"); return; }
        frameSlot.replaceChildren(el("iframe", { class: "pf-pdf-frame", src: pdfUrl, title: d.title }));
        readBtn.lastChild.textContent = t("hidePdf");
      } }, icon("book"), el("span", {}, t("readHere"))) : null;
      const { facts, free } = splitNotes(d.description);
      story.replaceChildren(
        drawerHead(d.title),
        el("div", { class: "pf-drawer-body" },
          el("div", { class: "pf-story-badges", style: "justify-content:flex-start" },
            d.pdf ? el("span", { class: "pf-pill carmine" }, "PDF") : null, langPill(d), d.year ? el("span", { class: "pf-pill quiet mono" }, d.year) : null),
          el("div", { class: "pf-story-actions", style: "justify-content:flex-start" }, readBtn,
            pdfUrl ? el("a", { class: "pf-btn", href: pdfUrl, target: "_blank", rel: "noopener" }, icon("external"), t("openPdf")) : null),
          frameSlot,
          imgs.map((src) => el("img", { class: "pf-reader-img", src, alt: "", loading: "lazy", title: t("zoom"), onclick: () => lightbox(src) })),
          el("dl", { class: "pf-facts" }, [[t("collection"), d.category_path], [t("label"), d.record_label || facts["label(s)"]]]
            .filter(([, v]) => v).map(([k, v]) => [el("dt", {}, k), el("dd", {}, v)])),
          free ? [el("h4", { class: "pf-notes-title" }, t("notes")), el("p", { class: "pf-notes" }, free)] : null,
          d.source_url ? el("p", { style: "margin-top:20px" }, el("a", { href: d.source_url, target: "_blank", rel: "noopener noreferrer" }, t("source"), " ", icon("external"))) : null));
      story.classList.add("wide");
      openDrawer(story);
    }


    // --- dedication -----------------------------------------------------------------------
    let ded = null;
    const SEAL = '<defs><radialGradient id="pfwax" cx="40%" cy="35%" r="70%"><stop offset="0" stop-color="#F2D58E"/>'
      + '<stop offset=".55" stop-color="#D4A853"/><stop offset="1" stop-color="#8A6326"/></radialGradient></defs>'
      + '<path d="M50 4c6 5 12 2 17 7s3 12 9 16 11 7 11 14-6 10-6 17 6 12 2 18-12 4-16 9-4 13-11 14-11-6-17-6-11 6-17 4-8-9-13-13-12-4-14-11 4-11 2-17-9-9-8-16 9-8 12-14 1-12 7-16 13 0 19-4 4-12 11-13 11 6 17 6z" fill="url(#pfwax)"/>'
      + '<circle cx="50" cy="52" r="27" fill="none" stroke="#7A5622" stroke-width="1.6" opacity=".7"/>'
      + '<circle cx="50" cy="52" r="22" fill="none" stroke="#FBE7B5" stroke-width=".8" opacity=".5"/>'
      + '<text x="50" y="62" text-anchor="middle" font-family="Cormorant Garamond, Georgia, serif" font-size="30" font-weight="700" fill="#5E3F14">П</text>';
    const dedVersion = () => String(hash(JSON.stringify(ded || {})));
    // Postal cancellation stamp (original drawing): ring text + date + star.
    function stampSvg() {
      const sv = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      sv.setAttribute("viewBox", "0 0 120 120"); sv.setAttribute("class", "pf-tg-stamp"); sv.setAttribute("aria-hidden", "true");
      const d = new Date();
      const dd = `${String(d.getDate()).padStart(2, "0")}.${String(d.getMonth() + 1).padStart(2, "0")}.${String(d.getFullYear()).slice(2)}`;
      sv.innerHTML = '<defs><path id="pfring" d="M60 60m-43 0a43 43 0 1 1 86 0a43 43 0 1 1-86 0"/></defs>'
        + '<circle cx="60" cy="60" r="55" fill="none" stroke="currentColor" stroke-width="2.4"/>'
        + '<circle cx="60" cy="60" r="34" fill="none" stroke="currentColor" stroke-width="1.6"/>'
        + '<text font-family="PT Mono, monospace" font-size="10.5" letter-spacing="1.6" fill="currentColor">'
        + '<textPath href="#pfring">ПАТЕФОН · АВТОРИЗОВАННЫЙ АРХИВ · СССР ·</textPath></text>'
        + '<path d="M60 33l3 7.2 7.8.6-6 5 1.9 7.6L60 49.3l-6.7 4.1 1.9-7.6-6-5 7.8-.6z" fill="currentColor"/>'
        + '<text x="60" y="76" text-anchor="middle" font-family="PT Mono, monospace" font-size="13" font-weight="700" fill="currentColor">' + dd + '</text>';
      return sv;
    }
    function openDedication() {
      const d = ded || {};
      const line = String(d.text || "").trim() || "A gift of old records";
      const seal = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      seal.setAttribute("viewBox", "0 0 100 104"); seal.setAttribute("class", "pf-ded-seal"); seal.setAttribute("aria-hidden", "true");
      seal.innerHTML = SEAL;
      const close = () => {
        store.set("dedicationSeen", dedVersion());
        wrap.classList.add("closing");
        setTimeout(() => wrap.remove(), reduceMotion ? 0 : 560);
      };
      const openBtn = el("button", { class: "pf-btn primary pf-ded-open", onclick: close }, t("dedOpen"));
      const wrap = el("div", { class: "pf-ded-wrap", role: "dialog", "aria-modal": "true", "aria-labelledby": "pf-ded-title",
        onclick: (e) => { if (e.target === wrap) close(); }, onkeydown: (e) => { if (e.key === "Escape") { e.stopPropagation(); close(); } } },
        el("article", { class: "pf-ded pf-ded-short pf-telegram" },
          el("div", { class: "pf-tg-head" },
            el("span", { class: "pf-tg-star", "aria-hidden": "true" }, icon("nStar")),
            el("span", {}, "ТЕЛЕГРАММА · TELEGRAM · ЖЕДЕЛХАТ")),
          el("div", { class: "pf-tg-meta mono" }, el("span", {}, "АВТОРИЗОВАННЫЙ АРХИВ"), el("span", {}, "ГОСТ 5289-50")),
          stampSvg(),
          el("h2", { id: "pf-ded-title", class: "pf-tg-text" }, line),
          openBtn));
      document.querySelectorAll(".pf-ded-wrap").forEach((n) => n.remove());
      document.body.append(wrap);
      openBtn.focus();
    }

    // --- keyboard ------------------------------------------------------------------------------
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { closeAll(); return; }
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const tag = (e.target.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select" || e.target.isContentEditable) return;
      if (document.querySelector("dialog[open]") || document.querySelector(".pf-ded-wrap")) return;
      const k = e.key.toLowerCase();
      if (k === " " || e.code === "Space") {
        if (tag === "button" || tag === "a") return;   // let the focused control click
        e.preventDefault(); togglePlay();
      } else if (k === "n") next(false);
      else if (k === "p") prev();
      else if (k === "s") toggleShuffle();
      else if (k === "r") cycleRepeat();
      else if (k === "m") toggleMute();
      else if (e.key === "/") { e.preventDefault(); searchInput.focus(); }
    });

    // --- render all ------------------------------------------------------------------------------
    function countRecentInSide() { renderSide(); }
    function renderAll() {
      renderSide(); renderTopbar(); renderHero(); renderContent(); renderPlayerLabels(); renderNowPlaying();
    }

    async function load_items() {
      const opt = (fn) => (fn ? fn().catch(() => ({})) : Promise.resolve({}));
      const [list, fits, lyrics, notes] = await Promise.all([adapter.items(), opt(adapter.fits), opt(adapter.lyrics), opt(adapter.notes)]);
      for (const it of list) {
        if (!it.fit && fits[it.id]) it.fit = fits[it.id];
        if (!it.lyrics && lyrics[it.id]) it.lyrics = lyrics[it.id];
        if (!it.notes && notes[it.id]) it.notes = notes[it.id];
      }
      S.items = list; S.byId = new Map(list.map((i) => [i.id, i]));
    }
    async function refresh() {
      const before = playable().length;
      const scrollTop = main.scrollTop;
      await load_items();
      renderSide(); renderHero(); renderContent(); renderNowPlaying();
      main.scrollTop = scrollTop;
      return playable().length - before;
    }

    document.documentElement.lang = S.ui === "kz" ? "kk" : S.ui;
    renderAll();
    requestAnimationFrame(frame);
    const ready = load_items().then(() => {
      if (S.view === "playlist" && !S.playlists.find((p) => p.id === S.arg)) { S.view = "all"; S.arg = null; }
      if (S.view === "lang" && !S.arg) S.view = "all";
      if (S.view === "radio") S.view = "all";   // radio starts on demand, not on page load
      renderAll();
      // The dedication greets her the first time (and again if its wording changes).
      Promise.resolve(typeof adapter.dedication === "function" ? adapter.dedication() : adapter.dedication)
        .catch(() => null).then((d) => {
          ded = d && typeof d === "object" ? d : null;
          if (store.get("dedicationSeen", null) !== dedVersion()) openDedication();
        });
    }).catch((err) => {
      put(content, empty(null, "Could not load the catalogue: " + (err && err.message ? err.message : err)));
    });

    return {
      ready, refresh, slot, toast,
      focused: () => (S.focusedId ? get(S.focusedId) : null),
      current: () => (S.currentId ? get(S.currentId) : null),
      count: () => playable().length,
      library: () => ({ playlists: S.playlists.map((p) => ({ id: p.id, name: p.name, tracks: p.tracks.slice(), created: p.created || "" })),
                        liked: [...S.liked] }),
    };
  }

  window.RRApp = { mount };
})();
