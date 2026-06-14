// ==UserScript==
// @name         OmegleApp Autonomous AI
// @match        https://omegleapp.me/*
// @match        https://nsfw.omegleapp.me/*
// @grant        none
// ==/UserScript==

(function () {
    'use strict';

    const GENERATE_URL = "http://localhost:5000/generate_reply";
    const SAVE_URL     = "http://localhost:5000/save_convo";

    let lastMsgId      = "";
    let isProcessing   = false;
    let isDisconnected = false;
    let sessionStart   = Date.now();

    console.log("[AVA] userscript loaded");

    // ── Utilities ──────────────────────────────────────────────────────────────

    function clearInput() {
        const input = document.querySelector('input.input[name="chatInput"]');
        if (!input) return;
        const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
        nativeSetter.call(input, '');
        input.dispatchEvent(new Event('input', { bubbles: true }));
    }

    function extractSender(row) {
        const el = row.querySelector('.text_auther');
        if (!el) return 'Unknown';
        if (el.classList.contains('You'))      return 'You';
        if (el.classList.contains('Stranger')) return 'Stranger';
        return el.innerText.trim();
    }

    function extractText(row) {
        const gifEl = row.querySelector('.text_gif img');
        if (gifEl) {
            try {
                const urlParam = new URL(gifEl.src, location.origin).searchParams.get('url');
                return urlParam ? decodeURIComponent(urlParam) : gifEl.src;
            } catch { return gifEl.src; }
        }
        const msgEl = row.querySelector('.text_msg');
        if (!msgEl) return '';
        let text = '';
        msgEl.childNodes.forEach(node => {
            if (node.nodeType === Node.TEXT_NODE) text += node.textContent;
            else if (node.nodeName === 'IMG' && node.alt) text += node.alt;
        });
        return text.replace(/^\s*:\s*/, '').trim();
    }

    function buildEnhancedHistory(rows) {
        const history = [];
        rows.forEach(row => {
            const sender = extractSender(row);
            if (sender === 'Host') return;
            const finalSender = sender === 'You' ? 'You' : 'Stranger';
            const gifImg = row.querySelector('.text_gif img');
            if (gifImg) {
                history.push({ sender: finalSender, type: "gif", gifUrl: gifImg.src });
            } else {
                const text = extractText(row);
                if (text) history.push({ sender: finalSender, message: text });
            }
        });
        return history;
    }

    // ── Typing ─────────────────────────────────────────────────────────────────

    async function typeRealistic(replyText, shouldAbort) {
        const input = document.querySelector('input.input[name="chatInput"]');
        if (!input) return;
        input.focus();

        const typoChars = {
            'a': 'sq', 'b': 'vn', 'c': 'xv', 'd': 'sf', 'e': 'wr',
            'f': 'dg', 'g': 'fh', 'h': 'gj', 'i': 'uo', 'j': 'hk',
            'k': 'jl', 'l': 'k', 'm': 'n', 'n': 'bm', 'o': 'ip',
            'p': 'o', 'q': 'w', 'r': 'et', 's': 'ad', 't': 'ry',
            'u': 'yi', 'v': 'cb', 'w': 'qe', 'x': 'zc', 'y': 'tu',
            'z': 'x'
        };

        function setValue(val) {
            const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
            nativeSetter.call(input, val);
            input.dispatchEvent(new Event('input', { bubbles: true }));
        }

        function randomDelay(min, max) {
            return new Promise(r => setTimeout(r, min + Math.random() * (max - min)));
        }

        async function backspace(count) {
            for (let i = 0; i < count; i++) {
                if (shouldAbort() || isDisconnected) return;
                setValue(input.value.slice(0, -1));
                await randomDelay(60, 120);
            }
        }

        for (let i = 0; i < replyText.length; i++) {
            if (isDisconnected || shouldAbort()) {
                setValue('');
                return;
            }

            const char     = replyText[i];
            const lower    = char.toLowerCase();
            const isLetter = /[a-z]/.test(lower);

            const makeTypo = isLetter && Math.random() < 0.12;

            if (makeTypo) {
                const wrongChars = typoChars[lower] || 'x';
                const wrongChar  = wrongChars[Math.floor(Math.random() * wrongChars.length)];

                setValue(input.value + wrongChar);
                await randomDelay(80, 160);

                const noticeDelay = Math.random() < 0.4;
                if (noticeDelay && i + 1 < replyText.length) {
                    setValue(input.value + replyText[i + 1]);
                    await randomDelay(60, 100);
                    await backspace(2);
                } else {
                    await backspace(1); 
                }

                await randomDelay(40, 80);
            }

            setValue(input.value + char);

            if (i === 0)                         await randomDelay(80, 180);
            else if (char === ' ')               await randomDelay(60, 140);
            else if (Math.random() < 0.05)       await randomDelay(200, 500); // occasional pause mid-word
            else                                 await randomDelay(25, 75);
        }

        if (isDisconnected || shouldAbort()) return setValue('');

        await randomDelay(200, 600);

        input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', keyCode: 13, bubbles: true }));
        const btn = document.querySelector('button.inputBox_btn.send');
        if (btn) { btn.removeAttribute('disabled'); btn.click(); }
    }

    // ── Message Processing ─────────────────────────────────────────────────────

    async function processMessage(chatName, history, metadata) {
        if (isProcessing) return;
        isProcessing = true;

        const respondingToStrangerId = lastMsgId;

        const strangerInterrupted = () => {
            const rows = document.querySelectorAll('.chatBox_messages .text');
            if (!rows.length) return false;
            const lastRow = rows[rows.length - 1];
            const sender  = extractSender(lastRow);
            const text    = extractText(lastRow);
            return (sender === 'Stranger' && `${sender}::${text}` !== respondingToStrangerId);
        };

        try {
            const res  = await fetch(GENERATE_URL, {
                method:  'POST',
                headers: { 'Content-Type': 'application/json' },
                body:    JSON.stringify({ chatName, history, metadata })
            });
            const data = await res.json();
            for (const r of (data.replies || [])) {
                if (isDisconnected || strangerInterrupted()) break;
                await new Promise(r => setTimeout(r, 500));
                await typeRealistic(r, strangerInterrupted);
            }
        } catch (e) { console.error('[ERROR]', e); }
        finally     { isProcessing = false; }
    }

    async function triggerDetection() {
        const rows = document.querySelectorAll('.chatBox_messages .text');
        if (!rows.length) return;

        const lastRow = rows[rows.length - 1];
        const sender  = extractSender(lastRow);
        const msgText = extractText(lastRow);
        const msgId   = `${sender}::${msgText}`;

        if (!msgId || msgId === lastMsgId) return;
        lastMsgId = msgId;

        if (sender === 'You' || sender === 'Host') return;

        const history = buildEnhancedHistory(rows);
        processMessage("Omegle Stranger", history, {
            sessionDuration: Math.floor((Date.now() - sessionStart) / 1000),
            messageCount:    history.length
        });
    }

    // ── Connection Handling ────────────────────────────────────────────────────

    function checkDisconnect() {
        const disconnectEl = document.querySelector('.link.socialLink');
        if (disconnectEl && disconnectEl.innerText.includes('disconnected') && !window._savedThisConvo) {
            window._savedThisConvo = true;
            isDisconnected = true;
            const history = buildEnhancedHistory(document.querySelectorAll('.chatBox_messages .text'));
            fetch(SAVE_URL, {
                method:  'POST',
                headers: { 'Content-Type': 'application/json' },
                body:    JSON.stringify({ history })
            }).finally(() => {
                clearInput();
                const btn = document.querySelector('button.inputBox_btn.success');
                if (btn) btn.click();
            });
        }
        if (!disconnectEl && isDisconnected) {
            window._savedThisConvo = false;
            isDisconnected = false;
            sessionStart   = Date.now();
            lastMsgId      = "";

            // reset memory for new stranger
            fetch("http://localhost:5000/reset", {
                method:  "POST",
                headers: { "Content-Type": "application/json" },
                body:    JSON.stringify({ chatName: "Omegle Stranger" })
            });
        }
    }

    // ── Observer ───────────────────────────────────────────────────────────────

    const observer = new MutationObserver(() => {
        clearTimeout(window._waTimer);
        window._waTimer = setTimeout(() => {
            triggerDetection();
            checkDisconnect();
        }, 250);
    });

    function waitForOmegle() {
        if (document.querySelector('.chatBox_messages')) {
            observer.observe(document.body, { childList: true, subtree: true });
            console.log('[READY] OmegleApp Autonomous AI Active');
        } else {
            setTimeout(waitForOmegle, 500);
        }
    }

    waitForOmegle();
})();