([listSel, boxSel, contentSel, limit]) => {
    const list = document.querySelector(listSel);
    if (!list) return [];
    const lr = list.getBoundingClientRect(), centerX = lr.x + lr.width / 2;
    const out = [], seenContent = new Set();
    const clean = v => String(v || '').replace(/\u200b/g, '').trim();
    const stableCover = src => {
        try { const u = new URL(src, location.href); return u.origin + u.pathname; }
        catch { return clean(src).split('?')[0]; }
    };
    const epoch = Math.random().toString(36).slice(2);
    let mediaIndex = 0;
    function identity(box, content) {
        const info = {message_id:'', ts:''};
        for (const el of [box, content]) {
            info.message_id ||= el.getAttribute('data-message-id') || el.getAttribute('data-msg-id') || '';
            info.ts ||= el.getAttribute('data-timestamp') || '';
        }
        // Only the message bound to this bubble; never traverse a conversation/list.
        let el = content;
        for (let depth=0; depth<7 && el; depth++, el=el.parentElement) {
            const pk = Object.keys(el).find(k=>k.startsWith('__reactProps'));
            const fk = Object.keys(el).find(k=>k.startsWith('__reactFiber'));
            const props = pk ? [el[pk]] : [];
            let f = fk && el[fk];
            for (let n=0;n<5 && f;n++,f=f.return) props.push(f.memoizedProps);
            for (const p of props) {
                const m = p?.message || p?.msg || p?.item?.message;
                if (!m || typeof m !== 'object') continue;
                const id = m.serverId || m.server_id || m.messageId || m.message_id || m.clientId || m.client_id || m.id;
                if (typeof id === 'string' || typeof id === 'number') info.message_id ||= String(id);
                const ts = m.createTime || m.create_time || m.createdAt;
                if (typeof ts === 'string' || typeof ts === 'number') info.ts ||= String(ts);
            }
            if (el===box) break;
        }
        return info;
    }
    function videoInfo(card, box) {
        const info = { id: '', url: '', title: '', publisher: '', cover: '' };
        info.publisher = clean(card.querySelector('[class*="authorName"], [class*="AuthorName"], [data-e2e="video-author"]')?.textContent);
        info.title = clean(card.querySelector('[class*="awemeDesc"], [class*="videoDesc"], [class*="VideoTitle"], [data-e2e="video-desc"]')?.textContent);
        const image = card.querySelector('img[class*="awemeContainer"], img[class*="cover"], img[class*="Cover"]') || [...card.querySelectorAll('img')].find(i => !/avatar/i.test(i.className + ' ' + i.src));
        info.cover = image ? stableCover(image.currentSrc || image.src) : '';
        const anchors = [card, ...card.querySelectorAll('a[href], [data-aweme-id], [data-video-id]')];
        for (const el of anchors) {
            const href = el.getAttribute('href') || '';
            const m = href.match(/(?:\/(?:video|note)\/|[?&](?:modal_id|aweme_id)=)(\d{10,25})/);
            const id = el.getAttribute('data-aweme-id') || el.getAttribute('data-video-id') || m?.[1];
            if (id && /^\d{10,25}$/.test(id)) { info.id = id; break; }
        }
        // React cards often have no link and display only the author. Read only
        // video metadata from this card's component props, never message lists.
        const seen = new WeakSet(); let objects = 0;
        function walk(value, path, depth) {
            if (!value || depth > 7 || objects > 220) return;
            if (typeof value === 'string') {
                if (value.length < 30000 && /^[\s]*[\[{]/.test(value)) {
                    try { walk(JSON.parse(value), path, depth + 1); } catch {}
                }
                return;
            }
            if (typeof value !== 'object' || value.nodeType || seen.has(value)) return;
            seen.add(value); objects++;
            if (Array.isArray(value) && value.length > 8) return;
            const id = value.aweme_id || value.awemeId || (/aweme|video|content|item/i.test(path) ? value.item_id || value.itemId : '');
            if (id && /^\d{10,25}$/.test(String(id))) {
                if (!info.id) info.id = String(id);
                if (!info.title) info.title = clean(value.content_title || value.desc || value.aweme_name || value.awemeName || value.title);
                if (!info.publisher) info.publisher = clean(value.content_name || value.author?.nickname || value.author?.name);
            }
            for (const [key, v] of Object.entries(value)) {
                if (['children', 'ref', '_owner', 'return', 'child', 'alternate', 'sibling', 'messages', 'messageList', 'conversation'].includes(key)) continue;
                walk(v, path + '.' + key, depth + 1);
            }
        }
        let el = card;
        for (let j = 0; j < 7 && el; j++, el = el.parentElement) {
            const pk = Object.keys(el).find(k => k.startsWith('__reactProps'));
            if (pk) walk(el[pk], 'card', 0);
            const fk = Object.keys(el).find(k => k.startsWith('__reactFiber'));
            let fiber = fk && el[fk];
            for (let n = 0; n < 5 && fiber; n++, fiber = fiber.return) walk(fiber.memoizedProps, 'card', 0);
            if (el === box) break;
        }
        if (info.id) info.url = 'https://www.douyin.com/video/' + info.id;
        return info;
    }
    function liveInfo(content, box) {
        const selector = '[class*="MessageItemShareLive"], [class*="MessageItemLiveCard"], [class*="MessageItemLivecontainer"], [data-e2e="share-live-card"], [data-e2e="live-room-card"], [data-room-id], a[href*="live.douyin.com/"]';
        const card = content.matches(selector) ? content : content.querySelector(selector);
        const target = card || content;
        const info = {room_id:'',web_rid:'',url:'',title:'',host:'',status:'',cover:'',card_text:''};
        let recognized = !!card;
        const liveUrl = value => {
            try {
                const u = new URL(value, location.href);
                if (u.protocol === 'https:' && u.hostname === 'live.douyin.com' && /^\/\d+\/?$/.test(u.pathname)) {
                    recognized = true;
                    info.url ||= u.origin + u.pathname;
                    info.web_rid ||= u.pathname.replace(/\//g,'');
                }
            } catch {}
        };
        for (const el of [target,...target.querySelectorAll('a[href], [data-room-id]')]) {
            const id = el.getAttribute('data-room-id');
            if (id && /^\d+$/.test(id)) { recognized = true; info.room_id ||= id; }
            liveUrl(el.getAttribute('href'));
        }
        // Read this bubble's own message content, not its author profile or list.
        const seen=new WeakSet();let count=0;
        function walk(value, depth) {
            if (!value || depth>6 || count>100) return;
            if (typeof value==='string') {
                if (value.length<30000 && /^[\s]*[\[{]/.test(value)) {
                    try {walk(JSON.parse(value),depth+1);} catch {}
                }
                return;
            }
            if (typeof value!=='object'||value.nodeType||seen.has(value)||Array.isArray(value)) return;
            seen.add(value);count++;
            const room = value.room_id || value.roomId || value.room_id_str || value.live_id || value.liveId;
            const web = value.web_rid || value.webRid;
            const type = String(value.content_type || value.contentType || value.card_type || '');
            const isRoom = (room && /^\d+$/.test(String(room))) || (web && /^\d+$/.test(String(web))) || /^(live|live_room|webcast|share_live)$/i.test(type);
            if (isRoom) recognized=true;
            for (const key of ['share_url','shareUrl','room_url','live_url','url']) if(typeof value[key]==='string') liveUrl(value[key]);
            if (recognized) {
                if (room) info.room_id ||= String(room);
                if (web) info.web_rid ||= String(web);
                info.title ||= clean(value.room_title || value.roomTitle || value.live_title || value.content_title || value.title);
                info.host ||= clean(value.anchor_name || value.anchorName || value.host_name || value.content_name || value.owner?.nickname || value.anchor?.nickname);
            }
            for (const [key,v] of Object.entries(value)) {
                if (['author','owner','anchor','user','sender','children','ref','_owner','return','child','alternate','sibling','messages','messageList','conversation'].includes(key)) continue;
                if(typeof v==='object'||typeof v==='string')walk(v,depth+1);
            }
        }
        let el=target;
        for(let j=0;j<7&&el;j++,el=el.parentElement){
            const pk=Object.keys(el).find(k=>k.startsWith('__reactProps'));
            const fk=Object.keys(el).find(k=>k.startsWith('__reactFiber'));
            const props=pk?[el[pk]]:[];let f=fk&&el[fk];
            for(let n=0;n<5&&f;n++,f=f.return)props.push(f.memoizedProps);
            for(const p of props){
                const m=p?.message||p?.msg||p?.item?.message;
                if(m){walk(m.parsedContent,0);walk(m.content,0);}
            }
            if(el===box)break;
        }
        if(!recognized)return null;
        info.host ||= clean(target.querySelector('[class*="authorName"], [class*="AuthorName"], [class*="anchorName"], [class*="AnchorName"], [data-e2e="live-host"]')?.textContent);
        info.title ||= clean(target.querySelector('[class*="liveTitle"], [class*="LiveTitle"], [class*="roomTitle"], [data-e2e="live-title"]')?.textContent);
        info.status = clean(target.querySelector('[class*="liveStatus"], [class*="LiveStatus"], [data-e2e="live-status"]')?.textContent);
        const cover=[...target.querySelectorAll('img')].find(i=>!/avatar/i.test(String(i.className)));
        if(cover)info.cover=stableCover(cover.currentSrc||cover.src);
        info.card_text=clean(target.innerText);
        // Server room ids and public web room ids are different; never invent a URL.
        if(!info.url&&info.web_rid&&/^\d+$/.test(info.web_rid))info.url='https://live.douyin.com/'+info.web_rid;
        return {card:target,info};
    }
    // Deduplicate and order the bubbles cheaply before parsing React/card data.
    // Keep the old bottom-to-top ordering, including stable ties in DOM order.
    const candidates = [];
    list.querySelectorAll(boxSel).forEach(box => {
        const br = box.getBoundingClientRect();
        if (!br.width && !br.height) return;
        const content = box.querySelector(contentSel) || box;
        if (seenContent.has(content)) return;
        seenContent.add(content);
        candidates.push({box, content, br});
    });
    candidates.sort((a,b)=>b.br.y-a.br.y);
    const selected = Number.isInteger(limit) && limit >= 0 ? candidates.slice(0, limit) : candidates;
    selected.forEach(({box, content, br}) => {
        const meta = identity(box, content);
        const cr = content.getBoundingClientRect();
        const explicitMine = box.matches('[class*="isFromMe"]') || !!box.querySelector('[class*="isFromMe"]');
        let mine = explicitMine;
        if (!explicitMine && cr.width > 0 && cr.width < lr.width * .8) mine = cr.x + cr.width / 2 > centerX;
        const live = liveInfo(content, box);
        if (live) {
            out.push({...meta,...identity(box,live.card),from_me:mine,y:br.y,kind:'live',live:live.info,text:'',img_src:''});
            return;
        }
        // Match whole placeholder bubbles only: ordinary sentences mentioning an
        // unavailable video must remain chat text. Metadata stays bubble-local.
        const visibleText = clean(content.innerText);
        const unavailableVideo = /^(?:视频无法查看|当前视频不可见|视频已失效|该视频已删除|视频已删除|该视频暂时无法查看)$/.test(visibleText);
        const card = content.querySelector('[class*="MessageItemShareAwemecontainer"], [class*="MessageItemVideo"], [data-e2e="share-video-card"], a[href*="/video/"], a[href*="/note/"]')
            || (content.matches('[class*="MessageItemShareAwemecontainer"]') ? content : null);
        if (unavailableVideo) {
            const video = videoInfo(card || content, box);
            video.card_status = 'unavailable';
            video.card_notice = visibleText;
            if (video.id) {
                out.push({...meta,from_me:mine,y:br.y,kind:'video',video,text:'',img_src:''});
            } else {
                out.push({...meta,from_me:mine,y:br.y,kind:'card',text:
                    '视频分享占位提示：' + visibleText + '\n网页未提供可确认的视频编号或链接，无法读取评论。这是界面提示，不代表好友说自己看不了视频；无法确认视频失效原因，也不要猜测视频内容。',img_src:''});
            }
            return;
        }
        if (card) {
            out.push({...meta,...identity(box,card),from_me:mine, y:br.y, kind:'video', video:videoInfo(card,box), text:'', img_src:''});
            return;
        }
        let imgEl = null;
        for (const im of content.querySelectorAll('img')) {
            const cls = String(im.className || ''), sr = im.currentSrc || im.src || '', r = im.getBoundingClientRect();
            const avatar = /avatar/i.test(cls) || !!im.closest('[class*="avatar"], [class*="Avatar"], [data-e2e="avatar"]');
            const inline = r.width <= 32 && r.height <= 32 && !!clean(content.innerText);
            const quoted = !!im.closest('[class*="ReplyPreview"], [class*="Quote"], [data-e2e="quoted-message"]');
            if (!avatar && !inline && !quoted && sr && r.width>0 && r.height>0) { imgEl = im; break; }
        }
        let text = clean(content.innerText);
        // Inline emoji belong to the text bubble; retain their visible alt labels.
        if (text && !imgEl) {
            const clone = content.cloneNode(true);
            clone.querySelectorAll('img').forEach(im=>im.replaceWith(document.createTextNode(im.alt || '')));
            text = clean(clone.textContent);
        }
        const classes = String(content.className || '') + ' ' + [...content.querySelectorAll('[class]')].map(e=>e.className).join(' ');
        let kind = text ? 'text' : 'unsupported';
        if (/voice|audio/i.test(classes) || content.querySelector('audio')) kind='voice';
        else if (/MessageItemFile|FileCard/i.test(classes)) kind='file';
        else if (/MessageItemLocation|LocationCard/i.test(classes)) kind='location';
        else if (/MessageItemShare|LinkCard|MessageItemCard/i.test(classes)) kind='card';
        else if (imgEl) kind=/sticker|emoticon|MessageItemEmoji|MessageItemEmotion/i.test(classes + ' ' + imgEl.src) ? 'sticker' : 'image';
        else if (/暂不支持|请到抖音手机端查看/.test(text)) kind='unsupported';
        const anchor = content.querySelector('a[href^="https://"],a[href^="http://"]');
        if (anchor && kind==='text') kind='link';
        let detail=text;
        if (anchor && !detail.includes(anchor.href)) detail += '\n链接：' + anchor.href;
        let token='';
        if (imgEl && (kind==='image'||kind==='sticker')) {
            token=epoch+'-'+(++mediaIndex);
            imgEl.setAttribute('data-douyin-reader-media',token);
            if (!detail) detail=clean(imgEl.alt);
        }
        out.push({...meta,from_me:mine,y:br.y,kind,text:detail,media_token:token,img_src:imgEl?(imgEl.currentSrc||imgEl.src||''):''});
    });
    out.sort((a,b)=>b.y-a.y);
    return out;
}
