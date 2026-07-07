(() => {
    const SITE_MUSIC_STATE_KEY = 'clover-site-music-state';
    const SITE_MUSIC_HOVER_CLOSE_DELAY_MS = 2400;
    const siteMusicTracksElement = document.getElementById('site-music-tracks');
    const siteMusicTracks = siteMusicTracksElement ? JSON.parse(siteMusicTracksElement.textContent) : [];
    const siteMusicPlayer = document.getElementById('siteMusicPlayer');
    const siteMusicToggle = document.getElementById('siteMusicToggle');
    const siteMusicCard = document.getElementById('siteMusicCard');
    const siteMusicAudio = document.getElementById('siteMusicAudio');
    const siteMusicTitle = document.getElementById('siteMusicTitle');
    const siteMusicToggleImage = document.getElementById('siteMusicToggleImage');
    const siteMusicCoverIcon = document.getElementById('siteMusicCoverIcon');
    const siteMusicLyrics = document.getElementById('siteMusicLyrics');
    const siteMusicPlay = document.getElementById('siteMusicPlay');
    const siteMusicPrev = document.getElementById('siteMusicPrev');
    const siteMusicNext = document.getElementById('siteMusicNext');
    const siteMusicProgress = document.getElementById('siteMusicProgress');
    const siteMusicShuffle = document.getElementById('siteMusicShuffle');
    const siteMusicRepeat = document.getElementById('siteMusicRepeat');
    const siteMusicListToggle = document.getElementById('siteMusicListToggle');
    const siteMusicList = document.getElementById('siteMusicList');
    let siteMusicCurrentIndex = 0;
    let siteMusicCurrentLyrics = [];
    let siteMusicActiveLyricIndex = -1;
    let siteMusicLastSavedSecond = -1;
    let siteMusicHoverCloseTimer = null;
    let siteMusicShuffleEnabled = false;
    let siteMusicRepeatMode = 'all';
    const siteMusicNextPreloader = new Audio();

    if (!siteMusicPlayer || !siteMusicAudio || !siteMusicTracks.length) {
        return;
    }

    function getSafeCssUrl(rawUrl) {
        const escapedUrl = String(rawUrl).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
        return `url("${escapedUrl}")`;
    }

    function readSiteMusicState() {
        try {
            return JSON.parse(sessionStorage.getItem(SITE_MUSIC_STATE_KEY) || '{}');
        } catch (error) {
            return {};
        }
    }

    function saveSiteMusicState() {
        const state = {
            trackIndex: siteMusicCurrentIndex,
            currentTime: siteMusicAudio.currentTime || 0,
            wasPlaying: !siteMusicAudio.paused,
            listOpen: !siteMusicList.hidden,
            repeatMode: siteMusicRepeatMode,
            shuffleEnabled: siteMusicShuffleEnabled,
        };
        sessionStorage.setItem(SITE_MUSIC_STATE_KEY, JSON.stringify(state));
    }

    function renderSiteMusicLyrics(activeIndex = 0) {
        if (activeIndex === siteMusicActiveLyricIndex) {
            return;
        }
        siteMusicActiveLyricIndex = activeIndex;
        siteMusicLyrics.innerHTML = '';
        if (!siteMusicCurrentLyrics.length) {
            const emptyLine = document.createElement('div');
            emptyLine.className = 'site-music-lyric-line active';
            emptyLine.textContent = '暂无歌词';
            siteMusicLyrics.appendChild(emptyLine);
            return;
        }

        siteMusicCurrentLyrics.forEach((lyricLine, lyricIndex) => {
            const lyricElement = document.createElement('div');
            lyricElement.className = 'site-music-lyric-line';
            lyricElement.textContent = lyricLine.text;
            if (lyricIndex === activeIndex) {
                lyricElement.classList.add('active');
            }
            siteMusicLyrics.appendChild(lyricElement);
        });
        const activeElement = siteMusicLyrics.querySelector('.site-music-lyric-line.active');
        if (activeElement) {
            activeElement.scrollIntoView({ block: 'nearest' });
        }
    }

    function getActiveLyricIndex(currentTime) {
        let activeIndex = 0;
        siteMusicCurrentLyrics.forEach((lyricLine, lyricIndex) => {
            if (lyricLine.time !== null && lyricLine.time <= currentTime) {
                activeIndex = lyricIndex;
            }
        });
        return activeIndex;
    }

    function updateSiteMusicPlayIcon() {
        const playIcon = siteMusicPlay.querySelector('i');
        const isPlaying = !siteMusicAudio.paused;
        playIcon.className = isPlaying ? 'fas fa-pause' : 'fas fa-play';
        siteMusicPlayer.classList.toggle('is-playing', isPlaying);
        saveSiteMusicState();
    }

    function updateSiteMusicProgress(percent) {
        const progressPercent = Math.max(0, Math.min(100, Number(percent) || 0));
        siteMusicProgress.style.setProperty('--site-music-progress-percent', `${progressPercent}%`);
        siteMusicProgress.setAttribute('aria-valuenow', String(Math.round(progressPercent)));
    }

    function getRandomSiteMusicIndex() {
        if (siteMusicTracks.length < 2) {
            return siteMusicCurrentIndex;
        }
        let nextTrackIndex = siteMusicCurrentIndex;
        while (nextTrackIndex === siteMusicCurrentIndex) {
            nextTrackIndex = Math.floor(Math.random() * siteMusicTracks.length);
        }
        return nextTrackIndex;
    }

    function getNextSiteMusicIndex(options = {}) {
        if (options.fromEnded && siteMusicRepeatMode === 'one') {
            return siteMusicCurrentIndex;
        }
        if (siteMusicShuffleEnabled) {
            return getRandomSiteMusicIndex();
        }
        return (siteMusicCurrentIndex + 1) % siteMusicTracks.length;
    }

    function getPreviousSiteMusicIndex() {
        if (siteMusicShuffleEnabled) {
            return getRandomSiteMusicIndex();
        }
        return (siteMusicCurrentIndex - 1 + siteMusicTracks.length) % siteMusicTracks.length;
    }

    function updateSiteMusicQueueControls() {
        siteMusicShuffle.classList.toggle('active', siteMusicShuffleEnabled);
        siteMusicShuffle.setAttribute('aria-pressed', String(siteMusicShuffleEnabled));
        siteMusicRepeat.classList.toggle('active', siteMusicRepeatMode === 'one');
        siteMusicRepeat.setAttribute('aria-pressed', String(siteMusicRepeatMode === 'one'));
        if (siteMusicRepeatMode === 'one') {
            siteMusicRepeat.setAttribute('aria-label', '单曲循环');
            siteMusicRepeat.setAttribute('title', '单曲循环');
        } else {
            siteMusicRepeat.setAttribute('aria-label', '循环全部');
            siteMusicRepeat.setAttribute('title', '循环全部');
        }
    }

    function preloadSiteMusicTrack(currentTrack) {
        if (currentTrack.is_web_playback) {
            siteMusicAudio.preload = 'auto';
            siteMusicAudio.load();
            return;
        }
        siteMusicAudio.preload = 'metadata';
    }

    function preloadSiteMusicNeighbor(trackIndex) {
        if (siteMusicTracks.length < 2) {
            return;
        }
        const nextTrack = siteMusicTracks[(trackIndex + 1) % siteMusicTracks.length];
        if (!nextTrack || !nextTrack.is_web_playback) {
            siteMusicNextPreloader.removeAttribute('src');
            return;
        }
        if (siteMusicNextPreloader.getAttribute('src') === nextTrack.audio_url) {
            return;
        }
        siteMusicNextPreloader.preload = 'auto';
        siteMusicNextPreloader.src = nextTrack.audio_url;
        siteMusicNextPreloader.load();
    }

    function renderSiteMusicPlaylist() {
        siteMusicList.innerHTML = '';
        siteMusicTracks.forEach((track, trackIndex) => {
            const trackButton = document.createElement('button');
            trackButton.className = 'site-music-track-item';
            trackButton.type = 'button';
            trackButton.dataset.trackIndex = String(trackIndex);
            if (trackIndex === siteMusicCurrentIndex) {
                trackButton.classList.add('active');
            }

            const trackNumber = document.createElement('span');
            trackNumber.className = 'site-music-track-index';
            trackNumber.textContent = String(trackIndex + 1).padStart(2, '0');

            const trackTitle = document.createElement('span');
            trackTitle.className = 'site-music-track-title';
            trackTitle.textContent = track.title;

            trackButton.appendChild(trackNumber);
            trackButton.appendChild(trackTitle);
            siteMusicList.appendChild(trackButton);
        });
    }

    function setSiteMusicCover(currentTrack) {
        if (currentTrack.cover_url) {
            const coverValue = getSafeCssUrl(currentTrack.cover_url);
            siteMusicPlayer.style.setProperty('--site-music-cover-image', coverValue);
            siteMusicCard.classList.add('has-cover');
            siteMusicToggleImage.src = currentTrack.cover_url;
            siteMusicToggleImage.hidden = false;
            siteMusicCoverIcon.hidden = true;
        } else {
            siteMusicPlayer.style.setProperty('--site-music-cover-image', 'none');
            siteMusicCard.classList.remove('has-cover');
            siteMusicToggleImage.removeAttribute('src');
            siteMusicToggleImage.hidden = true;
            siteMusicCoverIcon.hidden = false;
        }
    }

    function loadSiteMusicTrack(trackIndex, options = {}) {
        siteMusicCurrentIndex = (trackIndex + siteMusicTracks.length) % siteMusicTracks.length;
        const currentTrack = siteMusicTracks[siteMusicCurrentIndex];
        const wasSameSource = siteMusicAudio.getAttribute('src') === currentTrack.audio_url;
        siteMusicTitle.textContent = currentTrack.title;
        if (!wasSameSource) {
            siteMusicAudio.src = currentTrack.audio_url;
        }
        updateSiteMusicProgress(0);
        siteMusicCurrentLyrics = currentTrack.lyrics_lines || [];
        siteMusicActiveLyricIndex = -1;
        siteMusicLastSavedSecond = -1;
        renderSiteMusicLyrics(0);
        setSiteMusicCover(currentTrack);
        renderSiteMusicPlaylist();

        if (typeof options.currentTime === 'number' && options.currentTime > 0) {
            siteMusicAudio.addEventListener('loadedmetadata', () => {
                siteMusicAudio.currentTime = Math.min(options.currentTime, siteMusicAudio.duration || options.currentTime);
            }, { once: true });
        }
        if (options.autoPlay) {
            siteMusicAudio.preload = 'auto';
        } else {
            preloadSiteMusicTrack(currentTrack);
        }
        preloadSiteMusicNeighbor(siteMusicCurrentIndex);
        if (options.autoPlay) {
            playSiteMusicAudio();
        }
        updateSiteMusicPlayIcon();
    }

    function playSiteMusicAudio() {
        const playPromise = siteMusicAudio.play();
        if (playPromise) {
            playPromise.catch(() => {});
        }
    }

    function clearSiteMusicHoverCloseTimer() {
        if (siteMusicHoverCloseTimer) {
            clearTimeout(siteMusicHoverCloseTimer);
            siteMusicHoverCloseTimer = null;
        }
    }

    function openSiteMusicHoverDetail() {
        clearSiteMusicHoverCloseTimer();
        siteMusicPlayer.classList.add('is-hovering');
    }

    function scheduleSiteMusicHoverClose() {
        clearSiteMusicHoverCloseTimer();
        siteMusicHoverCloseTimer = setTimeout(() => {
            siteMusicPlayer.classList.remove('is-hovering');
            siteMusicHoverCloseTimer = null;
        }, SITE_MUSIC_HOVER_CLOSE_DELAY_MS);
    }

    siteMusicToggle.addEventListener('click', () => {
        const shouldOpen = !siteMusicPlayer.classList.contains('is-open');
        clearSiteMusicHoverCloseTimer();
        siteMusicPlayer.classList.toggle('is-open', shouldOpen);
        siteMusicPlayer.classList.toggle('is-hovering', shouldOpen);
        siteMusicToggle.setAttribute('aria-expanded', String(shouldOpen));
    });

    function removeSiteMusicDetail() {
        clearSiteMusicHoverCloseTimer();
        siteMusicPlayer.classList.remove('is-open');
        siteMusicPlayer.classList.remove('is-hovering');
        siteMusicToggle.setAttribute('aria-expanded', 'false');
    }

    function handleSiteMusicOutsideClick(event) {
        if (!siteMusicPlayer.contains(event.target)) {
            removeSiteMusicDetail();
        }
    }

    document.addEventListener('click', handleSiteMusicOutsideClick);
    siteMusicPlayer.addEventListener('mouseenter', openSiteMusicHoverDetail);
    siteMusicPlayer.addEventListener('mouseleave', scheduleSiteMusicHoverClose);

    siteMusicPlay.addEventListener('click', () => {
        if (siteMusicAudio.paused) {
            playSiteMusicAudio();
        } else {
            siteMusicAudio.pause();
        }
    });

    siteMusicPrev.addEventListener('click', () => {
        loadSiteMusicTrack(getPreviousSiteMusicIndex(), { autoPlay: true });
    });

    siteMusicNext.addEventListener('click', () => {
        loadSiteMusicTrack(getNextSiteMusicIndex(), { autoPlay: true });
    });

    siteMusicShuffle.addEventListener('click', () => {
        siteMusicShuffleEnabled = !siteMusicShuffleEnabled;
        updateSiteMusicQueueControls();
        saveSiteMusicState();
    });

    siteMusicRepeat.addEventListener('click', () => {
        siteMusicRepeatMode = siteMusicRepeatMode === 'one' ? 'all' : 'one';
        updateSiteMusicQueueControls();
        saveSiteMusicState();
    });

    siteMusicListToggle.addEventListener('click', () => {
        siteMusicList.hidden = !siteMusicList.hidden;
        siteMusicListToggle.classList.toggle('active', !siteMusicList.hidden);
        saveSiteMusicState();
    });

    siteMusicList.addEventListener('click', (event) => {
        const trackButton = event.target.closest('[data-track-index]');
        if (!trackButton) {
            return;
        }
        loadSiteMusicTrack(Number(trackButton.dataset.trackIndex), { autoPlay: true });
    });

    siteMusicAudio.addEventListener('play', updateSiteMusicPlayIcon);
    siteMusicAudio.addEventListener('pause', updateSiteMusicPlayIcon);
    siteMusicAudio.addEventListener('ended', () => {
        loadSiteMusicTrack(getNextSiteMusicIndex({ fromEnded: true }), { autoPlay: true });
    });
    siteMusicAudio.addEventListener('timeupdate', () => {
        if (siteMusicAudio.duration) {
            updateSiteMusicProgress((siteMusicAudio.currentTime / siteMusicAudio.duration) * 100);
        }
        renderSiteMusicLyrics(getActiveLyricIndex(siteMusicAudio.currentTime));
        const currentSecond = Math.floor(siteMusicAudio.currentTime || 0);
        if (currentSecond !== siteMusicLastSavedSecond) {
            siteMusicLastSavedSecond = currentSecond;
            saveSiteMusicState();
        }
    });

    function getSiteBoundaryNodes(rootDocument, boundaryName) {
        const startNode = rootDocument.querySelector(`[data-site-extra-css-boundary="${boundaryName}"]`);
        return startNode;
    }

    function getNodesBetweenBoundaries(startNode, endNode) {
        const boundaryNodes = [];
        let currentNode = startNode.nextSibling;
        while (currentNode && currentNode !== endNode) {
            boundaryNodes.push(currentNode);
            currentNode = currentNode.nextSibling;
        }
        return boundaryNodes;
    }

    function replaceSiteExtraCss(incomingDocument) {
        const currentStart = getSiteBoundaryNodes(document, 'start');
        const currentEnd = getSiteBoundaryNodes(document, 'end');
        const incomingStart = getSiteBoundaryNodes(incomingDocument, 'start');
        const incomingEnd = getSiteBoundaryNodes(incomingDocument, 'end');
        if (!currentStart || !currentEnd || !incomingStart || !incomingEnd) {
            return;
        }

        getNodesBetweenBoundaries(currentStart, currentEnd).forEach((node) => {
            node.remove();
        });
        getNodesBetweenBoundaries(incomingStart, incomingEnd).forEach((node) => {
            currentEnd.parentNode.insertBefore(document.importNode(node, true), currentEnd);
        });
    }

    async function executeSitePageScripts(scriptContainer) {
        const scriptElements = Array.from(scriptContainer.querySelectorAll('script'));
        for (const oldScript of scriptElements) {
            await new Promise((resolve) => {
                const newScript = document.createElement('script');
                Array.from(oldScript.attributes).forEach((attribute) => {
                    newScript.setAttribute(attribute.name, attribute.value);
                });
                if (oldScript.src) {
                    newScript.async = false;
                    newScript.onload = resolve;
                    newScript.onerror = resolve;
                } else {
                    newScript.textContent = `(function(){\n${oldScript.textContent}\n})();`;
                }
                oldScript.replaceWith(newScript);
                if (!oldScript.src) {
                    resolve();
                }
            });
        }
    }

    async function replaceSiteExtraScripts(incomingDocument) {
        const currentScripts = document.getElementById('site-page-extra-js');
        const incomingScripts = incomingDocument.getElementById('site-page-extra-js');
        if (!currentScripts || !incomingScripts) {
            return;
        }
        currentScripts.innerHTML = incomingScripts.innerHTML;
        await executeSitePageScripts(currentScripts);
    }

    function shouldSkipSiteUrl(url) {
        return (
            url.origin !== window.location.origin
            || url.pathname.startsWith('/admin/')
            || url.pathname.startsWith('/media/')
            || url.pathname.startsWith('/static/')
            || url.pathname === '/rss.xml'
        );
    }

    function shouldHandleSiteLink(event, anchorElement) {
        if (
            event.defaultPrevented
            || event.button !== 0
            || event.metaKey
            || event.ctrlKey
            || event.shiftKey
            || event.altKey
            || anchorElement.target
            || anchorElement.hasAttribute('download')
            || anchorElement.dataset.noSiteNavigation !== undefined
            || anchorElement.dataset.bsToggle
        ) {
            return false;
        }

        const hrefValue = anchorElement.getAttribute('href') || '';
        if (!hrefValue || hrefValue.startsWith('#') || hrefValue.startsWith('mailto:') || hrefValue.startsWith('tel:') || hrefValue.startsWith('javascript:')) {
            return false;
        }

        const targetUrl = new URL(anchorElement.href, window.location.href);
        if (shouldSkipSiteUrl(targetUrl)) {
            return false;
        }
        return !(targetUrl.pathname === window.location.pathname && targetUrl.search === window.location.search && targetUrl.hash);
    }

    function shouldHandleSiteForm(formElement, submitter) {
        const methodName = (submitter?.getAttribute('formmethod') || formElement.method || 'get').toUpperCase();
        const encodingType = (submitter?.getAttribute('formenctype') || formElement.enctype || '').toLowerCase();
        const targetValue = submitter?.getAttribute('formtarget') || formElement.target;
        const actionUrl = new URL(submitter?.getAttribute('formaction') || formElement.action || window.location.href, window.location.href);
        const hasFileInput = Boolean(formElement.querySelector('input[type="file"]'));

        return (
            formElement.dataset.noSiteNavigation === undefined
            && !targetValue
            && (methodName === 'GET' || methodName === 'POST')
            && !encodingType.includes('multipart/form-data')
            && !hasFileInput
            && !shouldSkipSiteUrl(actionUrl)
        );
    }

    async function renderSitePageFromResponse(response, options = {}) {
        const contentType = response.headers.get('content-type') || '';
        if (!contentType.includes('text/html')) {
            window.location.href = response.url;
            return;
        }

        const responseText = await response.text();
        const incomingDocument = new DOMParser().parseFromString(responseText, 'text/html');
        const incomingShell = incomingDocument.getElementById('site-page-shell');
        const currentShell = document.getElementById('site-page-shell');
        if (!incomingShell || !currentShell) {
            window.location.href = response.url;
            return;
        }

        document.title = incomingDocument.title;
        window.dispatchEvent(new CustomEvent('site:before-navigate'));
        replaceSiteExtraCss(incomingDocument);
        currentShell.innerHTML = incomingShell.innerHTML;
        await replaceSiteExtraScripts(incomingDocument);

        const finalUrl = response.url || options.url;
        if (options.historyMode === 'replace') {
            window.history.replaceState({}, '', finalUrl);
        } else if (options.historyMode !== 'none' && finalUrl !== window.location.href) {
            window.history.pushState({}, '', finalUrl);
        }

        if (!options.preserveScroll) {
            window.scrollTo(0, 0);
        }
        window.dispatchEvent(new CustomEvent('site:after-navigate', {
            detail: {
                url: finalUrl,
            },
        }));
    }

    async function fetchSitePage(url, options = {}) {
        document.documentElement.classList.add('site-page-loading');
        saveSiteMusicState();
        try {
            const response = await fetch(url, {
                method: options.method || 'GET',
                body: options.body || null,
                credentials: 'same-origin',
                headers: {
                    'X-Requested-With': 'XMLHttpRequest',
                },
            });
            await renderSitePageFromResponse(response, { ...options, url });
        } catch (error) {
            window.location.href = url;
        } finally {
            document.documentElement.classList.remove('site-page-loading');
        }
    }

    window.cloverSiteNavigate = fetchSitePage;

    function handleSiteNavigationClick(event) {
        const anchorElement = event.target.closest('a[href]');
        if (!anchorElement || !shouldHandleSiteLink(event, anchorElement)) {
            return;
        }

        event.preventDefault();
        fetchSitePage(anchorElement.href);
    }

    function getSiteFormData(formElement, submitter) {
        try {
            return new FormData(formElement, submitter);
        } catch (error) {
            return new FormData(formElement);
        }
    }

    function handleSiteNavigationSubmit(event) {
        const formElement = event.target;
        const submitter = event.submitter || null;
        if (!(formElement instanceof HTMLFormElement) || event.defaultPrevented || !shouldHandleSiteForm(formElement, submitter)) {
            return;
        }

        event.preventDefault();
        const methodName = (submitter?.getAttribute('formmethod') || formElement.method || 'get').toUpperCase();
        const actionUrl = new URL(submitter?.getAttribute('formaction') || formElement.action || window.location.href, window.location.href);
        const formData = getSiteFormData(formElement, submitter);

        if (methodName === 'GET') {
            const queryParams = new URLSearchParams(formData);
            actionUrl.search = queryParams.toString();
            fetchSitePage(actionUrl.href);
            return;
        }

        fetchSitePage(actionUrl.href, {
            method: 'POST',
            body: formData,
            historyMode: 'replace',
            preserveScroll: true,
        });
    }

    document.addEventListener('click', handleSiteNavigationClick);
    document.addEventListener('submit', handleSiteNavigationSubmit);
    window.addEventListener('popstate', () => {
        fetchSitePage(window.location.href, {
            historyMode: 'none',
        });
    });
    window.addEventListener('beforeunload', saveSiteMusicState);

    const restoredMusicState = readSiteMusicState();
    siteMusicShuffleEnabled = Boolean(restoredMusicState.shuffleEnabled);
    siteMusicRepeatMode = restoredMusicState.repeatMode === 'one' ? 'one' : 'all';
    updateSiteMusicQueueControls();
    const restoredTrackIndex = Number.isInteger(restoredMusicState.trackIndex) ? restoredMusicState.trackIndex : 0;
    loadSiteMusicTrack(restoredTrackIndex, {
        currentTime: Number(restoredMusicState.currentTime || 0),
    });
    if (restoredMusicState.listOpen) {
        siteMusicList.hidden = false;
        siteMusicListToggle.classList.add('active');
    }
    if (restoredMusicState.wasPlaying) {
        playSiteMusicAudio();
    }
})();
