/* SPDX-License-Identifier: MIT */
(() => {
  'use strict';
  const scriptUrl = document.currentScript.src;
  const labels = {
    en: { title: 'Open on your phone', link: 'Decision Companion', qr: 'QR code', address: 'WORKSPACE LINK',
      note: 'Scan this code to open the Companion. Sign in separately if the workspace requires it.',
      loopback: 'This address points to this device. Use a reachable workspace address to open it on another device.',
      local: 'The local file opens on this device. Use a reachable workspace address to open it on another device.',
      failed: 'The QR code could not be drawn. Use the link below.',
      copied: 'Link copied.', copyFailed: 'Select the link to copy it manually.', copy: 'Copy link', close: 'Close' },
    fr: { title: 'Ouvrir sur votre téléphone', link: 'Compagnon de décision', qr: 'Code QR', address: 'LIEN DE L’ESPACE',
      note: 'Scannez ce code pour ouvrir le Compagnon. Connectez-vous séparément si l’espace l’exige.',
      loopback: 'Cette adresse désigne cet appareil. Utilisez une adresse accessible depuis votre téléphone.',
      local: 'Le fichier local s’ouvre sur cet appareil. Utilisez une adresse accessible depuis votre téléphone.',
      failed: 'Le code QR est indisponible. Utilisez le lien ci-dessous.',
      copied: 'Lien copié.', copyFailed: 'Sélectionnez le lien pour le copier.', copy: 'Copier le lien', close: 'Fermer' }
  };
  const lang = () => document.documentElement.lang.toLowerCase().startsWith('fr') ? 'fr' : 'en';
  const target = () => {
    const url = new URL('index.html', scriptUrl);
    url.username = '';
    url.password = '';
    url.search = new URLSearchParams({ lang: lang() }).toString();
    url.hash = '';
    return url;
  };
  let library;
  const loadLibrary = () => {
    if (window.qrcode) return Promise.resolve(window.qrcode);
    if (library) return library;
    library = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = new URL('vendor/qrcode.js', scriptUrl).href;
      script.integrity = 'sha256-eeyG+ChWAFsciHkFz8z8++w4Icphx/1alS+qX3ePeRw=';
      script.referrerPolicy = 'no-referrer';
      script.onload = () => window.qrcode ? resolve(window.qrcode) : reject(new Error('QR unavailable'));
      script.onerror = () => reject(new Error('QR unavailable'));
      document.head.append(script);
    }).catch(error => { library = null; throw error; });
    return library;
  };

  const node = (tag, text) => {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    return element;
  };

  async function openDialog(trigger) {
    const strings = labels[lang()];
    const url = target();
    const dialog = node('dialog');
    dialog.className = 'companion-handoff';
    const brand = node('p', 'AI Factory / Companion');
    brand.className = 'companion-dialog-kicker';
    const heading = node('h2', strings.title);
    heading.id = 'companion-handoff-title';
    dialog.setAttribute('aria-labelledby', heading.id);
    const localFile = url.protocol === 'file:';
    const onLoopback = url.hostname === 'localhost' || url.hostname.endsWith('.localhost') ||
      url.hostname.startsWith('127.') || url.hostname === '[::1]';
    const note = node('p', localFile ? strings.local : onLoopback ? strings.loopback : strings.note);
    note.id = 'companion-handoff-note';
    dialog.setAttribute('aria-describedby', note.id);
    const canvas = node('canvas');
    canvas.hidden = true;
    canvas.setAttribute('aria-hidden', 'true');
    const link = node('a', url.href);
    link.href = url.href;
    link.referrerPolicy = 'no-referrer';
    const address = node('div');
    address.className = 'companion-address';
    address.append(node('p', strings.address), link);
    const buttons = node('div');
    buttons.className = 'companion-buttons';
    const copy = node('button', strings.copy);
    copy.className = 'companion-copy';
    const close = node('button', strings.close);
    copy.type = close.type = 'button';
    const status = node('p', '');
    status.className = 'companion-handoff-status';
    status.setAttribute('role', 'status');
    buttons.append(copy, close);
    dialog.append(brand, heading, note, canvas, address, buttons, status);
    dialog.addEventListener('keydown', event => event.stopPropagation());
    dialog.addEventListener('close', () => {
      dialog.remove();
      trigger.focus();
    }, { once: true });
    close.addEventListener('click', () => dialog.close());
    copy.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(url.href);
        status.textContent = strings.copied;
      } catch {
        status.textContent = strings.copyFailed;
      }
    });
    document.body.append(dialog);
    dialog.showModal();
    close.focus();
    if (localFile) return;
    try {
      const factory = await loadLibrary();
      if (!dialog.open) return;
      const qr = factory(0, 'M');
      qr.addData(url.href);
      qr.make();
      const cell = 5;
      const margin = cell * 4;
      const size = qr.getModuleCount();
      canvas.width = canvas.height = size * cell + margin * 2;
      const context = canvas.getContext('2d');
      context.fillStyle = '#fff';
      context.fillRect(0, 0, canvas.width, canvas.height);
      context.fillStyle = '#000';
      for (let row = 0; row < size; row++) {
        for (let column = 0; column < size; column++) {
          if (qr.isDark(row, column)) context.fillRect(column * cell + margin, row * cell + margin, cell, cell);
        }
      }
      canvas.hidden = false;
    } catch {
      if (dialog.open) status.textContent = strings.failed;
    }
  }

  const links = document.querySelectorAll('[data-companion-link]');
  const buttons = document.querySelectorAll('[data-companion-qr]');
  const translate = () => {
    const strings = labels[lang()];
    for (const link of links) {
      link.textContent = strings.link;
      link.href = target().href;
      link.referrerPolicy = 'no-referrer';
    }
    for (const button of buttons) button.textContent = strings.qr;
  };
  translate();
  new MutationObserver(translate).observe(document.documentElement, { attributes: true, attributeFilter: ['lang'] });
  for (const button of buttons) button.addEventListener('click', () => { void openDialog(button); });
})();
