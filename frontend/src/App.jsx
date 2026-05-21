import { useState, useEffect, useRef, useMemo } from 'react'
import axios from 'axios'
import './App.css'

function App() {
  const [events, setEvents] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [searchTerm, setSearchTerm] = useState('')
  const [platformFilter, setPlatformFilter] = useState('all')
  const [showFavoritesOnly, setShowFavoritesOnly] = useState(false)
  const [appMode, setAppMode] = useState('general')
  const [movies, setMovies] = useState([])
  const [selectedEvent, setSelectedEvent] = useState(null)
  const [selectedMovie, setSelectedMovie] = useState(null)
  const [movieShowtimes, setMovieShowtimes] = useState([])
  const [loadingShowtimes, setLoadingShowtimes] = useState(false)
  const [selectedDate, setSelectedDate] = useState('')
  const [selectedCinema, setSelectedCinema] = useState('')
  const [totalEvents, setTotalEvents] = useState(0);
  const [refreshTrigger, setRefreshTrigger] = useState(0);
  
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);
  const [currentPage, setCurrentPage] = useState(1);
  const itemsPerPage = 12;
  
  // 🎯 動態獲取 API 基礎網址，確保在手機或區網測試時不會因為 localhost 失敗而導致破圖或無法連線
  const API_BASE = `http://${window.location.hostname}:8000`;
  
  const [deviceId] = useState(() => {
    let id = localStorage.getItem('ticketing_device_id');
    if (!id) {
      id = 'device-' + Date.now() + '-' + Math.random().toString(36).substring(2, 9);
      localStorage.setItem('ticketing_device_id', id);
    }
    return id;
  });

  const [favorites, setFavorites] = useState(() => {
    const saved = localStorage.getItem('ticketing_favorites');
    return saved ? JSON.parse(saved) : [];
  });
  const [keywords, setKeywords] = useState(() => {
    const saved = localStorage.getItem('ticketing_keywords');
    return saved ? JSON.parse(saved) : [];
  });
  const [userEmail, setUserEmail] = useState(() => {
    return localStorage.getItem('ticketing_email') || '';
  });
  const [showKeywordModal, setShowKeywordModal] = useState(false);
  const [newKeyword, setNewKeyword] = useState('');

  const keywordsRef = useRef(keywords);
  useEffect(() => { keywordsRef.current = keywords; }, [keywords]);

    const getValidImageUrl = (url) => {
     if (!url || url === 'null' || url === 'undefined') return 'https://images.unsplash.com/photo-1489599849927-2ee91cede3ba?q=80&w=400&auto=format&fit=crop'; 
     
     // 🎯 如果網址已經是 Base64 格式，直接回傳，不再經過任何網路請求或代理！
     if (url.startsWith('data:')) return url;
    
    // 🎯 威秀防盜鏈，以及開眼相關 (photowant, wikia) 不支援 HTTPS，強制走後端代理突破 Mixed Content 限制！
    if (url.includes('vscinemas') || url.includes('atmovies') || url.includes('photowant') || url.includes('wikia') || url.includes('amazon')) {
      return `${API_BASE}/api/proxy-image?url=${encodeURIComponent(url)}`;
    }
    
    return url;
  };

  useEffect(() => {
    const handleResize = () => {
      if (window.innerWidth <= 768 && isSidebarCollapsed) {
        setIsSidebarCollapsed(false);
      }
    };
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [isSidebarCollapsed]);

  useEffect(() => {
    setCurrentPage(1);
    setPlatformFilter('all');
  }, [appMode]);

  useEffect(() => {
    setCurrentPage(1);
  }, [searchTerm, platformFilter, showFavoritesOnly]);

  useEffect(() => {
    localStorage.setItem('ticketing_email', userEmail);
  }, [userEmail]);

  useEffect(() => {
    const fetchEventsData = async () => {
      try {
        setLoading(true);
        const params = { skip: (currentPage - 1) * itemsPerPage, limit: itemsPerPage, search: searchTerm };
        if (showFavoritesOnly) {
          if (favorites.length === 0) {
            appMode === 'general' ? setEvents([]) : setMovies([]);
            setTotalEvents(0); setLoading(false); return;
          }
          params.fav_ids = favorites.join(',');
        }
        if (appMode === 'general') {
          params.platform = platformFilter === 'all' ? 'general_all' : platformFilter;
          const res = await axios.get(`${API_BASE}/api/events`, { params }).catch(() => ({ data: { events: [], total: 0 } }));
          setEvents(res.data.events || []);
          setTotalEvents(res.data.total || 0);
        } else {
          const res = await axios.get(`${API_BASE}/api/movies`, { params }).catch(() => ({ data: { movies: [], total: 0 } }));
          setMovies(res.data.movies || []);
          setTotalEvents(res.data.total || 0);
        }
        setError(null);
      } catch (err) {
        setError("系統發生錯誤，請稍後再試！");
      } finally {
        setLoading(false);
      }
    };
    const timeoutId = setTimeout(() => fetchEventsData(), 300);
    return () => clearTimeout(timeoutId);
  }, [currentPage, platformFilter, searchTerm, showFavoritesOnly, favorites, refreshTrigger, appMode]);

  useEffect(() => { localStorage.setItem('ticketing_favorites', JSON.stringify(favorites)); }, [favorites]);

  useEffect(() => {
    localStorage.setItem('ticketing_keywords', JSON.stringify(keywords));
    if (deviceId) {
      axios.post(`${API_BASE}/api/subscriptions`, { device_id: deviceId, keywords: keywords, email: userEmail })
        .catch(err => console.error("同步訂閱失敗", err));
    }
  }, [keywords, userEmail, deviceId]);

  useEffect(() => {
    if (selectedMovie) {
      setLoadingShowtimes(true);
      setMovieShowtimes([]);
      setSelectedDate('');
      setSelectedCinema('');
      axios.get(`${API_BASE}/api/movies/${selectedMovie.id}/showtimes`)
        .then(res => setMovieShowtimes(res.data || []))
        .catch(err => console.error("獲取時刻表失敗", err))
        .finally(() => setLoadingShowtimes(false));
    }
  }, [selectedMovie]);

  const availableDates = useMemo(() => {
    const dates = new Set();
    movieShowtimes.forEach(group => {
      if (selectedCinema && group.cinema !== selectedCinema) return;
      (group.showtimes || []).forEach(st => {
        const datePart = st.time.split(' ')[0];
        if (datePart) dates.add(datePart);
      });
    });
    return Array.from(dates).sort();
  }, [movieShowtimes, selectedCinema]);

  const availableCinemas = useMemo(() => {
    const cinemas = new Set();
    movieShowtimes.forEach(group => { if (group.cinema) cinemas.add(group.cinema); });
    return Array.from(cinemas).sort();
  }, [movieShowtimes]);

  useEffect(() => { if (selectedDate && !availableDates.includes(selectedDate)) setSelectedDate(''); }, [availableDates, selectedDate]);
  useEffect(() => { if (selectedCinema && !availableCinemas.includes(selectedCinema)) setSelectedCinema(''); }, [availableCinemas, selectedCinema]);

  const filteredShowtimes = useMemo(() => {
    if (!selectedDate || !selectedCinema) return [];
    const groups = movieShowtimes.filter(g => g.cinema === selectedCinema);
    if (groups.length === 0) return [];
    const allShowtimes = [];
    groups.forEach(group => {
      (group.showtimes || []).forEach(st => {
        if (st.time.startsWith(selectedDate)) {
          const timeOnly = st.time.split(' ')[1] || st.time;
          allShowtimes.push({ ...st, timeOnly });
        }
      });
    });
    return allShowtimes.sort((a, b) => a.timeOnly.localeCompare(b.timeOnly));
  }, [movieShowtimes, selectedCinema, selectedDate]);

  useEffect(() => {
    const ws = new WebSocket(`ws://${window.location.hostname}:8000/ws`);
    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.new_events && data.new_events.length > 0) {
          const currentKeywords = keywordsRef.current;
          data.new_events.forEach(title => {
            const matchedKw = currentKeywords.find(kw => title.toLowerCase().includes(kw.toLowerCase()));
            if (matchedKw) {
              if ("Notification" in window && Notification.permission === "granted") {
                new Notification("系統通知 | OmniTicket", { body: `您訂閱的「${matchedKw}」有新活動：\n${title}` });
              } else {
                alert(`[系統通知] OmniTicket\n\n您訂閱的「${matchedKw}」有新活動：\n${title}`);
              }
            }
          });
        }
      } catch (e) { console.error("解析通知失敗", e); }
      handleRefresh();
    };
    
    // 🎯 修復 React Strict Mode 導致的 WebSocket is closed before connection is established 錯誤
    return () => {
      if (ws.readyState === WebSocket.CONNECTING) {
        ws.addEventListener('open', () => ws.close());
      } else {
        ws.close();
      }
    };
  }, []);

  const handleRefresh = () => {
    setCurrentPage(1);
    setRefreshTrigger(prev => prev + 1);
  };
  
  const totalPages = Math.ceil(totalEvents / itemsPerPage) || 1;

  const toggleFavorite = (e, id) => {
    e.stopPropagation();
    setFavorites(prev => prev.includes(id) ? prev.filter(favId => favId !== id) : [...prev, id]);
  };

  const handleAddKeyword = (e) => {
    e.preventDefault();
    const kw = newKeyword.trim();
    if (kw && !keywords.includes(kw)) {
      setKeywords([...keywords, kw]);
      setNewKeyword('');
    }
    if ("Notification" in window && Notification.permission === "default") {
      Notification.requestPermission();
    }
  };
  const removeKeyword = (kwToRemove) => { setKeywords(keywords.filter(kw => kw !== kwToRemove)); };

  return (
    <div className="app-container">
      <div className={`mobile-overlay ${isMobileMenuOpen ? 'open' : ''}`} onClick={() => setIsMobileMenuOpen(false)}></div>
      
      <aside className={`sidebar ${isMobileMenuOpen ? 'open' : ''} ${isSidebarCollapsed ? 'collapsed' : ''}`}>
        <div className="brand">
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg>
          <h2>OmniTicket</h2>
        </div>
        
        <div className="search-bar" onClick={() => isSidebarCollapsed && setIsSidebarCollapsed(false)}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
          <input type="text" placeholder="搜尋..." value={searchTerm} onChange={(e) => setSearchTerm(e.target.value)} />
        </div>

        <div className="nav-menu">
          <p className="nav-title">瀏覽選項</p>
          <button className={`nav-btn ${!showFavoritesOnly ? 'active' : ''}`} onClick={() => setShowFavoritesOnly(false)}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><rect x="3" y="3" width="7" height="7"></rect><rect x="14" y="3" width="7" height="7"></rect><rect x="14" y="14" width="7" height="7"></rect><rect x="3" y="14" width="7" height="7"></rect></svg>
            <span className="nav-text">所有活動</span>
          </button>
          <button className={`nav-btn ${showFavoritesOnly ? 'active' : ''}`} onClick={() => setShowFavoritesOnly(true)}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"></path></svg>
            <span className="nav-text">我的收藏</span>
          </button>
        </div>

        {appMode === 'general' && (
          <div className="nav-menu">
            <p className="nav-title">售票平台</p>
            <button className={`nav-btn ${platformFilter === 'all' ? 'active' : ''}`} onClick={() => setPlatformFilter('all')}><span className="nav-dot all"></span><span className="nav-text">全部平台</span></button>
            <button className={`nav-btn ${platformFilter === 'kktix' ? 'active' : ''}`} onClick={() => setPlatformFilter('kktix')}><span className="nav-dot kktix"></span><span className="nav-text">KKTIX</span></button>
            <button className={`nav-btn ${platformFilter === 'tixcraft' ? 'active' : ''}`} onClick={() => setPlatformFilter('tixcraft')}><span className="nav-dot tixcraft"></span><span className="nav-text">拓元售票</span></button>
            <button className={`nav-btn ${platformFilter === 'ticketplus' ? 'active' : ''}`} onClick={() => setPlatformFilter('ticketplus')}><span className="nav-dot ticketplus"></span><span className="nav-text">遠大售票</span></button>
          </div>
        )}
      </aside>

      <main className="main-content">
        <header className="top-header">
          <div className="header-left">
            <button className="mobile-menu-btn" onClick={() => setIsMobileMenuOpen(true)}>
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="3" y1="12" x2="21" y2="12"></line><line x1="3" y1="6" x2="21" y2="6"></line><line x1="3" y1="18" x2="21" y2="18"></line></svg>
            </button>
            <button className="sidebar-toggle-btn hidden-mobile" onClick={() => setIsSidebarCollapsed(!isSidebarCollapsed)}>
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="3" y1="12" x2="21" y2="12"></line><line x1="3" y1="6" x2="21" y2="6"></line><line x1="3" y1="18" x2="21" y2="18"></line></svg>
            </button>
          </div>
          <div className="mode-switcher">
            <button className={`mode-btn ${appMode === 'general' ? 'active' : ''}`} onClick={() => setAppMode('general')}>一般售票</button>
            <button className={`mode-btn ${appMode === 'movie' ? 'active' : ''}`} onClick={() => setAppMode('movie')}>電影時刻</button>
          </div>
          <div className="header-actions">
            <button className="btn-outline hidden-mobile" onClick={() => setShowKeywordModal(true)}>訂閱通知</button>
            <button className="btn-primary" onClick={handleRefresh}>刷新</button>
          </div>
        </header>

        <div className="content-scroll">
          {error && <div className="message error">{error}</div>}
          
          {loading && !error ? (
            <div className="message"><div className="spinner"></div><p>系統載入中...</p></div>
          ) : (
            <div className="event-grid">
              {(appMode === 'general' ? events : movies).map(item => (
                <div key={item.id} className="event-card" onClick={() => appMode === 'general' ? setSelectedEvent(item) : setSelectedMovie(item)}>
                  <div className="card-image-wrap">
                    {item.cover_image_url ? (
                      <img src={getValidImageUrl(item.cover_image_url)} alt={item.title} className="card-image" referrerPolicy="no-referrer" onError={(e) => { e.target.onerror = null; e.target.src = "https://images.unsplash.com/photo-1489599849927-2ee91cede3ba?q=80&w=400&auto=format&fit=crop"; }} />
                    ) : (
                      <div className="card-no-image">圖片載入中</div>
                    )}
                    {appMode === 'general' && item.source_platform && (
                      <span className={`platform-badge ${item.source_platform}`}>{item.source_platform.toUpperCase()}</span>
                    )}
                    <button className={`fav-btn ${favorites.includes(item.id) ? 'active' : ''}`} onClick={(e) => toggleFavorite(e, item.id)}>❤</button>
                  </div>
                  <div className="card-info">
                    <h3 className="card-title">{item.title}</h3>
                    <div className="card-footer">
                      <span className="card-action-text">{appMode === 'general' ? '查看詳細資訊' : '查看時刻表'}</span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
          
          {!loading && !error && (appMode === 'general' ? events : movies).length > 0 && (
            <div className="pagination">
              <button className="page-btn" disabled={currentPage === 1} onClick={() => setCurrentPage(p => p - 1)}>上一頁</button>
              <span className="page-info">第 {currentPage} 頁 / 共 {totalPages} 頁</span>
              <button className="page-btn" disabled={currentPage === totalPages} onClick={() => setCurrentPage(p => p + 1)}>下一頁</button>
            </div>
          )}
        </div>
      </main>

      {/* 活動詳情 Modal */}
      {selectedEvent && (
        <div className="modal-overlay" onClick={() => setSelectedEvent(null)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <button className="modal-close" onClick={() => setSelectedEvent(null)}>✕</button>
            <img src={getValidImageUrl(selectedEvent.cover_image_url)} className="modal-img" alt="cover" referrerPolicy="no-referrer" onError={(e) => { e.target.onerror = null; e.target.src = "https://images.unsplash.com/photo-1489599849927-2ee91cede3ba?q=80&w=400&auto=format&fit=crop"; }} />
            <div className="modal-body">
              {selectedEvent.source_platform && (
                <span className={`platform-badge ${selectedEvent.source_platform} inline`}>{selectedEvent.source_platform.toUpperCase()}</span>
              )}
              <h2 className="modal-title">{selectedEvent.title}</h2>
              <div className="modal-desc">
                {selectedEvent.description ? <pre>{selectedEvent.description}</pre> : <p>詳細資訊載入中...</p>}
              </div>
              {selectedEvent.external_url && <a href={selectedEvent.external_url} target="_blank" rel="noopener noreferrer" className="btn-primary block">前往官方網站</a>}
            </div>
          </div>
        </div>
      )}

      {/* 電影時刻表 Modal */}
      {selectedMovie && (
        <div className="modal-overlay" onClick={() => setSelectedMovie(null)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <button className="modal-close" onClick={() => setSelectedMovie(null)}>✕</button>
            <img src={getValidImageUrl(selectedMovie.cover_image_url)} className="modal-img contain" alt="cover" referrerPolicy="no-referrer" onError={(e) => { e.target.onerror = null; e.target.src = "https://images.unsplash.com/photo-1489599849927-2ee91cede3ba?q=80&w=400&auto=format&fit=crop"; }} />
            <div className="modal-body">
              <span className="platform-badge atmovies inline">電影時刻</span>
              <h2 className="modal-title">{selectedMovie.title}</h2>
              
              <div className="modal-desc transparent">
                {selectedMovie.release_date && <p className="release-date">上映日期：{selectedMovie.release_date}</p>}
                <h3 className="section-title">放映時刻表</h3>
                
                {loadingShowtimes ? (
                  <div className="message"><div className="spinner small"></div></div>
                ) : movieShowtimes.length > 0 ? (
                  <div className="showtime-container">
                    <div className="filter-row">
                      <select value={selectedCinema} onChange={e => { setSelectedCinema(e.target.value); setSelectedDate(''); }}>
                        <option value="" disabled>1. 請選擇影城</option>
                        {availableCinemas.map(c => <option key={c} value={c}>{c}</option>)}
                      </select>
                      <select value={selectedDate} onChange={e => setSelectedDate(e.target.value)} disabled={!selectedCinema}>
                        <option value="" disabled>{!selectedCinema ? '請先選擇影城' : '2. 請選擇日期'}</option>
                        {availableDates.map(d => <option key={d} value={d}>{d}</option>)}
                      </select>
                    </div>
                    
                    <div className="times-grid">
                      {!selectedDate || !selectedCinema ? (
                        <p className="system-msg">請先選擇上方影城與日期，即可查看放映場次。</p>
                      ) : filteredShowtimes.length > 0 ? (
                        filteredShowtimes.map(st => (
                          <a key={st.id} href={st.booking_url} target="_blank" rel="noopener noreferrer" className="time-btn">{st.timeOnly}</a>
                        ))
                      ) : (
                        <p className="system-msg">此日期無放映場次。</p>
                      )}
                    </div>
                  </div>
                ) : (
                  <p className="system-msg">目前該電影暫無時刻表資料。</p>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 訂閱 Modal */}
      {showKeywordModal && (
        <div className="modal-overlay" onClick={() => setShowKeywordModal(false)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <button className="modal-close" onClick={() => setShowKeywordModal(false)}>✕</button>
            <div className="modal-body">
              <h2 className="modal-title">關鍵字 Email 訂閱</h2>
              <p className="modal-subtitle">設定您的 Email 並加入關注的關鍵字。未來有符合的活動上架，系統將第一時間寄信通知您。</p>
              
              <div className="form-group">
                <input type="email" placeholder="輸入接收通知的 Email" value={userEmail} onChange={(e) => setUserEmail(e.target.value)} />
              </div>

              <form onSubmit={handleAddKeyword} className="form-group row">
                <input type="text" placeholder="輸入關鍵字 (例: 周杰倫)" value={newKeyword} onChange={(e) => setNewKeyword(e.target.value)} />
                <button type="submit" className="btn-primary">新增</button>
              </form>

              <div className="chips-container">
                {keywords.map(kw => (
                  <span key={kw} className="chip">
                    {kw} <button onClick={() => removeKeyword(kw)}>
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
                    </button>
                  </span>
                ))}
                {keywords.length === 0 && <span className="empty-hint">目前尚未訂閱任何關鍵字</span>}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default App
