(function() {
  'use strict';

  // ==================== 配置 ====================
  var API_BASE = window.__UBA_API_BASE || '';
  var POLL_INTERVAL = 5000; // 5秒
  var MAX_LOG_ENTRIES = 8;

  // ==================== 状态 ====================
  var pollTimer = null;
  var pollInFlight = false;
  var nextPollAt = 0;
  var lastRequestCost = null;
  var currentLogAsyncId = null;
  var isPaused = false;
  var isManualMode = false;
  var logHistory = [];
  var userData = null;        // 缓存 /api/users
  var userRanking = null;     // 排序后的用户ID列表
  var currentTab = 'monitor';
  var tableSortBy = 'avg_risk';
  var tableAsc = false;

  // ECharts 实例
  var gaugeChart = null;
  var radarChart = null;
  var shapChart = null;
  var modelCompareChart = null;
  var alertQualityChart = null;
  var securityMetricChart = null;
  var featureImportanceChart = null;
  var curveChart = null;
  var experimentLoaded = false;

  // vis-network 实例
  var network = null;
  var networkData = null;

  // ==================== DOM 引用 ====================
  var $ = function(id) { return document.getElementById(id); };

  // ==================== Tab 切换 ====================
  function initTabs() {
    var tabs = document.querySelectorAll('.nav-tab');
    tabs.forEach(function(tab) {
      tab.addEventListener('click', function() {
        var tabName = this.getAttribute('data-tab');
        switchTab(tabName);
      });
    });
  }

  function switchTab(tabName) {
    currentTab = tabName;

    // 更新导航按钮状态
    document.querySelectorAll('.nav-tab').forEach(function(t) {
      t.classList.toggle('active', t.getAttribute('data-tab') === tabName);
    });

    // 更新标签页内容
    document.querySelectorAll('.tab-content').forEach(function(tc) {
      tc.classList.toggle('active', tc.id === 'tab-' + tabName);
    });

    // 暂停/恢复轮询
    if (tabName === 'monitor') {
      if (!isPaused && !isManualMode) startPolling();
      resizeCharts();
    } else {
      stopPolling();
    }

    // 按需加载
    if (tabName === 'table' && !userData) {
      loadUserTable();
    }
    if (tabName === 'detail' && !userData) {
      loadUserDetailDropdown();
    }
    if (tabName === 'graph') {
      loadGraph();
    }
    if (tabName === 'experiment') {
      setTimeout(function() {
        loadExperiment();
        resizeCharts();
      }, 0);
    }
  }

  // ==================== Tab 1: 实时监控 ====================
  function initMonitor() {
    // 初始化 ECharts
    var theme = document.documentElement.classList.contains('theme-light') ? 'light' : 'dark';
    gaugeChart = echarts.init($('gaugeChart'), theme);
    radarChart = echarts.init($('radarChart'), theme);
    shapChart = echarts.init($('shapChart'), theme);

    // 初始占位
    renderGauge(0);
    renderRadarPlaceholder();
    renderShapPlaceholder();

    // 按钮
    $('modeBtn').addEventListener('click', toggleMonitorMode);
    $('nextLogBtn').addEventListener('click', fetchManualNextLog);
    $('pauseBtn').addEventListener('click', togglePause);
    $('themeBtn').addEventListener('click', toggleTheme);
    $('retryBtn').addEventListener('click', function() { fetchNextLog(true); });
    updateModeControls();
    updatePollMeta();

    // 启动轮询
    startPolling();
  }

  function startPolling() {
    if (pollTimer || isPaused || isManualMode) return;
    $('pollIntervalLabel').textContent = '间隔 ' + Math.round(POLL_INTERVAL / 1000) + 's';
    if (!nextPollAt) nextPollAt = Date.now();
    pollTimer = setInterval(function() {
      updatePollMeta();
      if (!pollInFlight && Date.now() >= nextPollAt) {
        fetchNextLog(false);
      }
    }, 250);
    if (logHistory.length === 0) {
      fetchNextLog(true);
    }
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
    updatePollMeta();
  }

  function scheduleNextPoll(delayMs) {
    nextPollAt = Date.now() + (typeof delayMs === 'number' ? delayMs : POLL_INTERVAL);
    updatePollMeta();
  }

  function updatePollMeta() {
    var countdown = $('pollCountdown');
    var cost = $('requestCost');
    if (countdown) {
      if (isManualMode) {
        countdown.textContent = '手动模式';
      } else if (isPaused) {
        countdown.textContent = '已暂停';
      } else if (pollInFlight) {
        countdown.textContent = '请求中';
      } else if (!nextPollAt) {
        countdown.textContent = '下次 --';
      } else {
        var remain = Math.max(0, Math.ceil((nextPollAt - Date.now()) / 1000));
        countdown.textContent = '下次 ' + remain + 's';
      }
    }
    if (cost) {
      cost.textContent = lastRequestCost == null ? '耗时 --' : '耗时 ' + lastRequestCost.toFixed(1) + 's';
    }
  }

  function togglePause() {
    isPaused = !isPaused;
    $('pauseBtn').textContent = isPaused ? '▶' : '⏸';
    $('statusPill').className = 'status-pill' + (isPaused ? ' paused' : '');
    $('statusPill').textContent = isPaused ? '● 已暂停' : '● 已连接';
    if (isPaused) {
      stopPolling();
    } else {
      scheduleNextPoll(0);
      startPolling();
    }
    updateModeControls();
  }

  function toggleMonitorMode() {
    isManualMode = !isManualMode;
    if (isManualMode) {
      stopPolling();
      isPaused = false;
    } else {
      isPaused = false;
      scheduleNextPoll(0);
      startPolling();
    }
    updateModeControls();
    updatePollMeta();
  }

  function updateModeControls() {
    var modeBtn = $('modeBtn');
    var nextBtn = $('nextLogBtn');
    var pauseBtn = $('pauseBtn');
    if (modeBtn) {
      modeBtn.textContent = isManualMode ? '手动' : '自动';
      modeBtn.classList.toggle('active', isManualMode);
    }
    if (nextBtn) {
      nextBtn.disabled = !isManualMode || pollInFlight;
      nextBtn.classList.toggle('active', isManualMode);
    }
    if (pauseBtn) {
      pauseBtn.disabled = isManualMode;
      pauseBtn.textContent = isPaused ? '▶' : '⏸';
    }
  }

  function fetchManualNextLog() {
    if (!isManualMode || pollInFlight) return;
    fetchNextLog(true);
  }

  function toggleTheme() {
    var html = document.documentElement;
    var isLight = html.classList.toggle('theme-light');
    localStorage.setItem('uba-theme', isLight ? 'light' : 'dark');
    var theme = isLight ? 'light' : 'dark';
    if (gaugeChart) gaugeChart.dispose();
    if (radarChart) radarChart.dispose();
    if (shapChart) shapChart.dispose();
    gaugeChart = echarts.init($('gaugeChart'), theme);
    radarChart = echarts.init($('radarChart'), theme);
    shapChart = echarts.init($('shapChart'), theme);
    if (logHistory.length > 0) {
      var last = logHistory[logHistory.length - 1];
      renderGauge(last.risk_score);
      renderRadar(last.raw_features);
      renderShap(last.top_causes);
    } else {
      renderGauge(0);
      renderRadarPlaceholder();
      renderShapPlaceholder();
    }
  }

  function fetchNextLog(force) {
    if (pollInFlight) return;
    pollInFlight = true;
    updateModeControls();
    var startedAt = performance.now();
    $('statusPill').className = 'status-pill loading';
    $('statusPill').textContent = '● 请求中';
    updatePollMeta();
    fetch(API_BASE + '/api/next_log')
      .then(function(r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function(data) {
        if (data.error) throw new Error(data.error);
        lastRequestCost = (performance.now() - startedAt) / 1000;
        $('statusPill').className = 'status-pill connected';
        $('statusPill').textContent = '● 已连接';
        $('refreshTime').textContent = new Date().toLocaleTimeString();
        hideError();
        updateDashboard(data);
        requestLlmSupplement(data);
      })
      .catch(function(err) {
        lastRequestCost = (performance.now() - startedAt) / 1000;
        console.error('轮询失败:', err);
        $('statusPill').className = 'status-pill error';
        $('statusPill').textContent = '● 连接失败';
        showError(err.message || '获取数据失败');
      })
      .finally(function() {
        pollInFlight = false;
        if (!isManualMode) {
          scheduleNextPoll(force ? POLL_INTERVAL : POLL_INTERVAL);
        } else {
          nextPollAt = 0;
          updatePollMeta();
        }
        updateModeControls();
      });
  }

  function updateDashboard(data) {
    logHistory.push(data);
    if (logHistory.length > MAX_LOG_ENTRIES) logHistory.shift();
    currentLogAsyncId = data.llm_async && data.llm_async.request_id ? data.llm_async.request_id : null;

    renderGauge(data.risk_score);
    renderRadar(data.raw_features);
    renderShap(data.top_causes);
    renderAiPanel(data);
    renderLogStrip();
  }

  function requestLlmSupplement(data) {
    if (!data || !data.llm_async || !data.llm_async.enabled || !data.llm_async.request_id) return;
    var requestId = data.llm_async.request_id;
    data.llm_async.status = 'loading';
    if (currentLogAsyncId === requestId) renderAiPanel(data);

    fetch(API_BASE + '/api/realtime_llm/' + encodeURIComponent(requestId))
      .then(function(r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function(extra) {
        var target = null;
        for (var i = 0; i < logHistory.length; i++) {
          if (logHistory[i].llm_async && logHistory[i].llm_async.request_id === requestId) {
            target = logHistory[i];
            break;
          }
        }
        if (!target) return;
        if (extra.ai_summary) target.ai_summary = extra.ai_summary;
        if (extra.alert_triage) target.alert_triage = extra.alert_triage;
        target.llm_async.status = 'done';
        if (currentLogAsyncId === requestId) {
          renderAiPanel(target);
          renderLogStrip();
        }
      })
      .catch(function(err) {
        console.warn('异步 LLM 补充失败:', err);
        for (var i = 0; i < logHistory.length; i++) {
          if (logHistory[i].llm_async && logHistory[i].llm_async.request_id === requestId) {
            logHistory[i].llm_async.status = 'error';
            if (currentLogAsyncId === requestId) renderAiPanel(logHistory[i]);
            break;
          }
        }
      });
  }

  function renderGauge(score) {
    var isAlert = score >= 0.5;
    gaugeChart.setOption({
      series: [{
        type: 'gauge',
        min: 0, max: 1,
        splitNumber: 5,
        progress: { show: true, width: 12, itemStyle: { color: isAlert ? '#e74c3c' : '#2ecc71' } },
        axisLine: { lineStyle: { width: 12, color: [[0.5, '#2ecc71'], [0.75, '#f39c12'], [1, '#e74c3c']] } },
        axisTick: { show: false },
        splitLine: { length: 8, lineStyle: { color: '#555' } },
        axisLabel: { color: '#aaa', fontSize: 10, formatter: function(v) { return v.toFixed(1); } },
        pointer: { length: '60%', width: 4, itemStyle: { color: isAlert ? '#e74c3c' : '#2ecc71' } },
        detail: {
          valueAnimation: true,
          formatter: '{value}',
          color: isAlert ? '#e74c3c' : '#2ecc71',
          fontSize: 22,
          fontWeight: 'bold',
        },
        data: [{ value: parseFloat(score.toFixed(3)) }]
      }]
    });
  }

  function renderRadarPlaceholder() {
    radarChart.setOption({
      radar: {
        indicator: [
          { name: '敏感操作频率', max: 1 },
          { name: '总操作频率', max: 1 },
          { name: '文件敏感度', max: 1 },
          { name: '深夜活动', max: 1 },
          { name: '周末活动', max: 1 },
        ],
        splitArea: { areaStyle: { color: ['rgba(46,204,113,0.02)', 'rgba(46,204,113,0.05)'] } },
        axisLine: { lineStyle: { color: 'rgba(255,255,255,0.15)' } },
        splitLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } },
        name: { textStyle: { color: '#aaa', fontSize: 10 } },
      },
      series: [{
        type: 'radar',
        data: [{ value: [0, 0, 0, 0, 0], name: '基线偏离', areaStyle: { color: 'rgba(46,204,113,0.2)' }, lineStyle: { color: '#2ecc71', width: 1 } }],
        symbol: 'none',
      }]
    });
  }

  function renderRadar(features) {
    if (!features) return renderRadarPlaceholder();
    radarChart.setOption({
      radar: {
        indicator: [
          { name: '敏感操作频率', max: 1 },
          { name: '总操作频率', max: 1 },
          { name: '文件敏感度', max: 1 },
          { name: '深夜活动', max: 1 },
          { name: '周末活动', max: 1 },
        ],
        splitArea: { areaStyle: { color: ['rgba(46,204,113,0.02)', 'rgba(46,204,113,0.05)'] } },
        axisLine: { lineStyle: { color: 'rgba(255,255,255,0.15)' } },
        splitLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } },
        name: { textStyle: { color: '#aaa', fontSize: 10 } },
      },
      series: [{
        type: 'radar',
        data: [{
          value: [
            parseFloat(features.sensitive_op_count_1h || 0),
            parseFloat(features.op_count_1h || 0),
            parseFloat(features.file_sensitive_level || 0),
            parseFloat(features.is_night || 0),
            parseFloat(features.is_weekend || 0),
          ],
          name: '基线偏离',
          areaStyle: { color: 'rgba(46,204,113,0.2)' },
          lineStyle: { color: '#2ecc71', width: 1 },
        }],
        symbol: 'none',
      }]
    });
  }

  function renderShapPlaceholder() {
    shapChart.setOption({
      xAxis: { type: 'value', show: false },
      yAxis: { type: 'category', show: false },
      series: [{ type: 'bar', data: [] }],
      title: { text: '等待数据…', left: 'center', top: 'center', textStyle: { color: '#666', fontSize: 13 } },
    });
  }

  function renderShap(causes) {
    if (!causes || causes.length === 0) return renderShapPlaceholder();
    var names = causes.map(function(c) { return c.feature; }).reverse();
    var values = causes.map(function(c) { return c.contribution; }).reverse();
    var colors = values.map(function(v) { return v >= 0 ? '#e74c3c' : '#2ecc71'; });

    shapChart.setOption({
      title: { text: '', show: false },
      grid: { left: '3%', right: '8%', top: '10%', bottom: '8%', containLabel: true },
      xAxis: { type: 'value', axisLabel: { color: '#aaa', fontSize: 9 } },
      yAxis: {
        type: 'category',
        data: names,
        axisLabel: { color: '#ccc', fontSize: 10 },
        axisLine: { show: false },
        axisTick: { show: false },
      },
      series: [{
        type: 'bar',
        data: values.map(function(v, i) { return { value: v, itemStyle: { color: colors[i] } }; }),
        barWidth: 14,
        label: {
          show: true,
          position: 'right',
          formatter: function(p) { return p.value.toFixed(3); },
          color: '#aaa',
          fontSize: 9,
        },
      }]
    });
  }

  function renderAiPanel(data) {
    var summary = data.ai_summary || {};
    $('aiTitle').textContent = summary.title || '智能解读';
    var meta = '用户 ' + data.user_id + ' | ' + data.timestamp;
    if (data.actual_label !== undefined) {
      meta += ' | 模拟标签: ' + (data.actual_label === 1 ? '异常' : '正常');
    }
    if (data.llm_async && data.llm_async.enabled) {
      if (data.llm_async.status === 'loading') meta += ' | AI补充生成中';
      if (data.llm_async.status === 'done') meta += ' | AI补充完成';
      if (data.llm_async.status === 'error') meta += ' | AI补充失败，已保留本地解读';
    }
    $('aiMeta').textContent = meta;

    if (summary.text) {
      $('aiText').textContent = summary.text;
    } else {
      $('aiText').textContent = '风险评分: ' + data.risk_score.toFixed(3) + ' | 警报: ' + (data.is_alert ? '是' : '否');
    }

    var ul = $('aiBullets');
    ul.innerHTML = '';
    if (summary.bullets && summary.bullets.length > 0) {
      summary.bullets.forEach(function(b) {
        var li = document.createElement('li');
        li.textContent = b;
        ul.appendChild(li);
      });
    }
    if (data.llm_async && data.llm_async.enabled && data.llm_async.status === 'loading') {
      var pending = document.createElement('li');
      pending.textContent = '外接大模型正在异步补充解读，图表与本地结果已先展示。';
      ul.appendChild(pending);
    }

    renderTriageCard(data.alert_triage);
  }

  function renderTriageCard(triage) {
    var card = $('triageCard');
    if (!card) return;
    if (!triage) {
      card.style.display = 'none';
      return;
    }

    card.style.display = 'block';
    $('triageScenario').textContent = triage.scenario || '常规风险观察';
    $('triageConfidence').textContent = '置信度 ' + Math.round(Number(triage.confidence || 0) * 100) + '%';

    var evidence = $('triageEvidence');
    var actions = $('triageActions');
    evidence.innerHTML = '';
    actions.innerHTML = '';

    (triage.evidence || []).forEach(function(text) {
      var li = document.createElement('li');
      li.textContent = text;
      evidence.appendChild(li);
    });
    (triage.recommended_actions || []).forEach(function(text) {
      var li = document.createElement('li');
      li.textContent = text;
      actions.appendChild(li);
    });
  }

  function renderLogStrip() {
    var strip = $('logStrip');
    strip.innerHTML = '';
    logHistory.forEach(function(log) {
      var chip = document.createElement('div');
      chip.className = 'log-chip' + (log.is_alert ? ' alert' : '');
      chip.innerHTML =
        '<div class="chip-user">' + log.user_id + '</div>' +
        '<div class="chip-score">' + log.risk_score.toFixed(3) + '</div>' +
        '<div class="chip-time">' + (log.timestamp ? log.timestamp.slice(5, 19) : '') + '</div>';
      strip.appendChild(chip);
    });
  }

  function showError(msg) {
    $('errorBar').style.display = 'flex';
    $('errorMsg').textContent = msg;
  }

  function hideError() {
    $('errorBar').style.display = 'none';
  }

  function resizeCharts() {
    if (gaugeChart) gaugeChart.resize();
    if (radarChart) radarChart.resize();
    if (shapChart) shapChart.resize();
    if (modelCompareChart) modelCompareChart.resize();
    if (alertQualityChart) alertQualityChart.resize();
    if (securityMetricChart) securityMetricChart.resize();
    if (featureImportanceChart) featureImportanceChart.resize();
    if (curveChart) curveChart.resize();
  }

  // ==================== Tab 2: 异常检测表 ====================
  function initTableTab() {
    $('tableSortBy').addEventListener('change', function() {
      tableSortBy = this.value;
      renderUserTable();
    });
    $('riskThreshold').addEventListener('input', function() {
      $('thresholdLabel').textContent = parseFloat(this.value).toFixed(2);
      renderUserTable();
    });
    $('userSearch').addEventListener('input', renderUserTable);
  }

  function loadUserTable() {
    $('tableLoading').style.display = 'block';
    $('anomalyTable').style.display = 'none';
    fetch(API_BASE + '/api/users')
      .then(function(r) { return r.json(); })
      .then(function(data) {
        userData = data;
        $('tableLoading').style.display = 'none';
        $('anomalyTable').style.display = 'table';
        renderUserTable();
        // 也用于用户详情下拉框
        populateDetailSelect();
      })
      .catch(function(err) {
        $('tableLoading').textContent = '加载失败：' + err.message;
      });
  }

  function renderUserTable() {
    if (!userData) return;
    var threshold = parseFloat($('riskThreshold').value);
    var search = $('userSearch').value.trim().toLowerCase();
    var sortField = $('tableSortBy').value;

    var filtered = userData.filter(function(u) {
      if (threshold > 0 && u.avg_risk < threshold) return false;
      if (search && !u.user_id.toLowerCase().includes(search)) return false;
      return true;
    });

    // 排序
    filtered.sort(function(a, b) {
      var va = a[sortField] || 0;
      var vb = b[sortField] || 0;
      if (typeof va === 'string') return va.localeCompare(vb);
      return vb - va; // 降序
    });

    // 重新编号
    filtered.forEach(function(u, i) { u.rank = i + 1; });

    var tbody = $('tableBody');
    tbody.innerHTML = '';
    filtered.forEach(function(u) {
      var tr = document.createElement('tr');
      tr.className = u.is_alert ? 'row-alert' : '';
      tr.innerHTML =
        '<td>' + u.rank + '</td>' +
        '<td><a href="#" class="user-link" data-user="' + u.user_id + '">' + u.user_id + '</a></td>' +
        '<td class="risk-cell' + (u.avg_risk >= 0.5 ? ' risk-high' : ' risk-low') + '">' + u.avg_risk.toFixed(4) + '</td>' +
        '<td>' + u.max_risk.toFixed(4) + '</td>' +
        '<td>' + u.alert_count + '</td>' +
        '<td>' + u.log_count + '</td>' +
        '<td>' + u.actual_alert_count + '</td>' +
        '<td>' + (u.is_alert ? '<span class="badge badge-alert">⚠ 告警</span>' : '<span class="badge badge-ok">✓ 正常</span>') + '</td>' +
        '<td class="causes-cell">' + (u.top_causes ? u.top_causes.map(function(c) { return c.feature; }).join(', ') : '-') + '</td>';
      tbody.appendChild(tr);

      // 点击用户跳转到详情
      tr.querySelector('.user-link').addEventListener('click', function(e) {
        e.preventDefault();
        var uid = this.getAttribute('data-user');
        selectUserDetail(uid);
      });
    });

    $('tableCount').textContent = '共 ' + filtered.length + ' 个用户';
  }

  // ==================== Tab 3: 用户详情 ====================
  function initDetailTab() {
    $('detailUserSelect').addEventListener('change', function() {
      var uid = this.value;
      if (uid) {
        loadUserDetail(uid);
      } else {
        $('detailContent').style.display = 'none';
        $('detailEmpty').style.display = 'block';
      }
    });
  }

  function loadUserDetailDropdown() {
    if (!userData) {
      fetch(API_BASE + '/api/users')
        .then(function(r) { return r.json(); })
        .then(function(data) {
          userData = data;
          populateDetailSelect();
        });
    } else {
      populateDetailSelect();
    }
  }

  function populateDetailSelect() {
    if (!userData) return;
    var sel = $('detailUserSelect');
    sel.innerHTML = '<option value="">— 请选择 —</option>';
    userData.forEach(function(u) {
      var opt = document.createElement('option');
      opt.value = u.user_id;
      opt.textContent = u.user_id + ' (风险: ' + u.avg_risk.toFixed(3) + ')';
      sel.appendChild(opt);
    });
  }

  function selectUserDetail(uid) {
    // 切换到详情标签
    switchTab('detail');
    $('detailUserSelect').value = uid;
    loadUserDetail(uid);
  }

  function loadUserDetail(uid) {
    $('detailEmpty').style.display = 'none';
    $('detailContent').style.display = 'none';
    fetch(API_BASE + '/api/user/' + encodeURIComponent(uid))
      .then(function(r) { return r.json(); })
      .then(function(d) {
        $('detailAvgRisk').textContent = d.avg_risk.toFixed(4);
        $('detailMaxRisk').textContent = d.max_risk.toFixed(4);
        $('detailAlertCount').textContent = d.alert_count;
        $('detailLogCount').textContent = d.log_count;

        if (d.is_alert) {
          $('detailAlertBadge').style.display = 'flex';
          $('detailAlertStatus').textContent = '⚠ 告警';
          $('detailAlertStatus').style.color = '#e74c3c';
        } else {
          $('detailAlertBadge').style.display = 'none';
        }

        renderUserProfile(d.profile_summary || {});
        $('detailFeatures').textContent = JSON.stringify(d.features, null, 2);
        $('detailShap').textContent = JSON.stringify(d.top_causes, null, 2);

        // 最近日志
        var tbody = $('recentLogsBody');
        tbody.innerHTML = '';
        if (d.recent_logs && d.recent_logs.length > 0) {
          d.recent_logs.forEach(function(log) {
            var tr = document.createElement('tr');
            tr.className = log.is_alert ? 'row-alert' : '';
            var causes = log.top_causes ? log.top_causes.map(function(c) { return c.feature; }).join(', ') : '-';
            tr.innerHTML =
              '<td>' + log.timestamp + '</td>' +
              '<td class="risk-cell' + (log.risk_score >= 0.5 ? ' risk-high' : ' risk-low') + '">' + log.risk_score.toFixed(4) + '</td>' +
              '<td>' + (log.is_alert ? '<span class="badge badge-alert">⚠ 告警</span>' : '<span class="badge badge-ok">✓ 正常</span>') + '</td>' +
              '<td class="causes-cell">' + causes + '</td>';
            tbody.appendChild(tr);
          });
        } else {
          tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:#888;">暂无日志记录</td></tr>';
        }

        $('detailContent').style.display = 'block';
      })
      .catch(function(err) {
        $('detailEmpty').style.display = 'block';
        $('detailEmpty').textContent = '加载失败：' + err.message;
      });
  }

  function renderUserProfile(profile) {
    $('profileLevel').textContent = profile.level || '常规监控用户';
    $('profileText').textContent = profile.text || '该用户当前缺少足够画像数据，请结合最近日志继续观察。';

    var factors = $('profileFactors');
    factors.innerHTML = '';
    (profile.key_factors || []).forEach(function(f) {
      var span = document.createElement('span');
      span.textContent = f;
      factors.appendChild(span);
    });

    var actions = $('profileActions');
    actions.innerHTML = '';
    (profile.attention_points || []).forEach(function(a) {
      var li = document.createElement('li');
      li.textContent = a;
      actions.appendChild(li);
    });
  }

  // ==================== Tab 4: 风险关系图 ====================
  function initGraphTab() {
    $('graphRefreshBtn').addEventListener('click', function() {
      if (network) {
        network.destroy();
        network = null;
      }
      loadGraph();
    });
  }

  function loadGraph() {
    $('graphLoading').style.display = 'block';
    fetch(API_BASE + '/api/graph')
      .then(function(r) { return r.json(); })
      .then(function(data) {
        $('graphLoading').style.display = 'none';
        renderGraph(data);
      })
      .catch(function(err) {
        $('graphLoading').textContent = '加载失败：' + err.message;
      });
  }

  function renderGraph(data) {
    // vis-network 节点和边数据
    var nodes = new vis.DataSet(data.nodes.map(function(n) {
      var nodeDef = {
        id: n.id,
        label: n.label,
        size: n.size || 15,
        font: { color: '#eee', size: 11, face: 'monospace' },
      };
      if (n.color) {
        nodeDef.color = { background: n.color, border: n.color };
      } else if (n.group === 'operation') {
        nodeDef.color = { background: '#2ecc71', border: '#27ae60' };
      } else {
        nodeDef.color = { background: '#3498db', border: '#2980b9' };
      }
      if (n.risk_score !== undefined) {
        nodeDef.title = '用户：' + n.id + '<br>风险评分：' + n.risk_score.toFixed(4);
        if (n.is_alert) {
          nodeDef.title += '<br>⚠ 告警用户';
        }
      }
      return nodeDef;
    }));

    var edges = new vis.DataSet(data.edges.map(function(e) {
      return {
        from: e.from,
        to: e.to,
        width: e.width || 1,
        dashes: e.dashed || false,
        color: { color: e.dashed ? '#555' : '#777', opacity: 0.5 },
        smooth: { type: 'continuous' },
      };
    }));

    networkData = { nodes: nodes, edges: edges };

    var options = {
      physics: {
        enabled: true,
        stabilization: { enabled: true, fit: true, iterations: 1500, updateInterval: 50 },
        barnesHut: {
          gravitationalConstant: -3000,
          centralGravity: 0.1,
          springLength: 200,
          springConstant: 0.04,
          damping: 0.85,
        },
      },
      interaction: {
        hover: true,
        tooltipDelay: 100,
        navigationButtons: false,
        zoomView: true,
        dragView: true,
      },
      layout: { improvedLayout: true },
      edges: {
        smooth: { type: 'continuous' },
      },
      nodes: {
        borderWidth: 1,
        shape: 'dot',
      },
    };

    var container = $('graphContainer');
    network = new vis.Network(container, networkData, options);
  }

  // ==================== Tab 5: 实验对比 ====================
  var MODEL_CN = {
    'Logistic Regression': '逻辑回归',
    'Isolation Forest': '孤立森林',
    'LightGBM': 'LightGBM',
    'LightGBM + Expert Rules': 'LightGBM + 专家规则',
  };

  var FEATURE_EXPLAIN = {
    role_code: '用户所属角色会决定正常操作边界，是判断越权行为的基础。',
    ip_location_code: '登录网络或地理位置异常时，账号盗用和远程接入风险上升。',
    operation_type_code: '不同操作动作本身风险不同，例如权限变更、批量导出、登录失败等。',
    hour: '发生时段反映行为是否偏离日常工作节奏。',
    dayofweek: '工作日和周末的行为模式不同，周末异常操作更值得关注。',
    is_weekend: '周末活动通常更少，若叠加高频或敏感操作会提高风险。',
    is_night: '深夜行为更容易与异常登录、批量下载和横向移动相关。',
    is_after_work: '非工作时间行为可作为异常上下文信号。',
    file_sensitive_level: '资源越敏感，误用、越权或泄露后果越严重。',
    op_count_1h: '短时间内操作频率突然升高，可能代表批量操作或扫描式探索。',
    sensitive_op_count_1h: '近 1 小时高敏操作越多，越可能触发数据泄露风险。',
    login_fail_count_1h: '登录失败聚集常见于暴力破解、凭证尝试或账号异常。',
    sensitive_download_count_1h: '短时间敏感下载/导出聚集，是数据外传的重要信号。',
    distinct_operation_count_1h: '操作类型突然变多，可能代表横向探索或异常会话。',
    operation_entropy_1h: '操作复杂度越高，说明行为序列越不像单一正常业务流程。',
    sensitive_level_delta_user: '访问资源敏感度高于该用户历史基线时，风险会上升。',
    op_count_delta_user: '操作频次超过该用户历史基线时，说明行为节奏异常。',
    is_remote_or_unknown_ip: '远程、未知或可疑网络位置会增加账号被盗用的可能性。',
    role_operation_mismatch: '当前操作超出角色常规权限范围，是越权访问的强信号。',
  };

  function initExperimentTab() {
    if (modelCompareChart && alertQualityChart && securityMetricChart && featureImportanceChart && curveChart) return;
    var theme = document.documentElement.classList.contains('theme-light') ? 'light' : 'dark';
    if ($('modelCompareChart')) modelCompareChart = echarts.init($('modelCompareChart'), theme);
    if ($('alertQualityChart')) alertQualityChart = echarts.init($('alertQualityChart'), theme);
    if ($('securityMetricChart')) securityMetricChart = echarts.init($('securityMetricChart'), theme);
    if ($('featureImportanceChart')) featureImportanceChart = echarts.init($('featureImportanceChart'), theme);
    if ($('curveChart')) curveChart = echarts.init($('curveChart'), theme);
  }

  function escapeHtml(s) {
    return String(s || '').replace(/[&<>"']/g, function(ch) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch];
    });
  }

  function modelName(row) {
    var raw = row.Model || row.model || '';
    return MODEL_CN[raw] || raw;
  }

  function metricValue(row, key) {
    return Number(row[key] || row[key.replace('-', '')] || row[key.toLowerCase()] || 0);
  }

  function pct(v) {
    return (Number(v || 0) * 100).toFixed(2) + '%';
  }

  function bestBy(rows, key) {
    if (!rows || rows.length === 0) return null;
    return rows.reduce(function(best, row) {
      return metricValue(row, key) > metricValue(best, key) ? row : best;
    }, rows[0]);
  }

  function bestOverall(rows) {
    if (!rows || rows.length === 0) return null;
    return rows.reduce(function(best, row) {
      var score = (metricValue(row, 'F1-Score') + metricValue(row, 'AUC') + metricValue(row, 'PR-AUC')) / 3;
      var bestScore = (metricValue(best, 'F1-Score') + metricValue(best, 'AUC') + metricValue(best, 'PR-AUC')) / 3;
      return score > bestScore ? row : best;
    }, rows[0]);
  }

  function confusion(row) {
    return row.ConfusionMatrix || row.confusion_matrix || row.confusion || null;
  }

  function loadExperiment() {
    if (!modelCompareChart) initExperimentTab();
    if (experimentLoaded) {
      resizeCharts();
      return;
    }
    $('experimentHint').style.display = 'block';
    Promise.all([
      fetch(API_BASE + '/api/experiment_results').then(function(r) { return r.json(); }),
      fetch(API_BASE + '/api/feature_importance').then(function(r) { return r.json(); })
    ])
      .then(function(all) {
        var exp = all[0] || {};
        var importance = all[1] || {};
        if (exp.error) {
          $('experimentHint').textContent = exp.error;
        } else {
          $('experimentHint').style.display = 'none';
        }
        var rows = exp.model_compare || [];
        renderExperimentSummary(exp, rows);
        renderModelCompare(rows);
        renderAlertQuality(rows);
        renderSecurityMetrics(rows);
        renderConfusionMatrix(rows);
        renderFeatureImportance(importance.shap_mean_abs || []);
        renderCurves(exp.roc_curve || [], exp.pr_curve || []);
        experimentLoaded = true;
        setTimeout(resizeCharts, 0);
      })
      .catch(function(err) {
        $('experimentHint').style.display = 'block';
        $('experimentHint').textContent = '实验结果加载失败：' + err.message;
      });
  }

  function renderExperimentSummary(exp, rows) {
    if (!rows || rows.length === 0) return;
    var best = bestOverall(rows);
    var recall = bestBy(rows, 'Recall');
    var precision = bestBy(rows, 'Precision');
    var ratio = Number(exp.positive_ratio || 0);

    $('expBestModel').textContent = modelName(best);
    $('expBestModelNote').textContent = 'F1 ' + pct(metricValue(best, 'F1-Score')) + ' / AUC ' + pct(metricValue(best, 'AUC'));
    $('expBestRecall').textContent = modelName(recall);
    $('expBestRecallNote').textContent = '召回率 ' + pct(metricValue(recall, 'Recall')) + '，漏报相对更少';
    $('expBestPrecision').textContent = modelName(precision);
    $('expBestPrecisionNote').textContent = '精确率 ' + pct(metricValue(precision, 'Precision')) + '，误报压力更低';
    $('expPositiveRatio').textContent = ratio ? pct(ratio) : '--';

    var insights = Array.isArray(exp.insights) ? exp.insights.slice(0, 5) : [];
    if (insights.length === 0) {
      var lgb = rows.find(function(r) { return r.Model === 'LightGBM'; });
      var hybrid = rows.find(function(r) { return r.Model === 'LightGBM + Expert Rules'; });
      insights.push('综合来看，' + modelName(best) + '在 F1、AUC 与 PR-AUC 上表现最均衡，适合作为主模型。');
      insights.push(modelName(recall) + '的召回率最高，说明真实异常被抓住的比例最高，适合强调少漏报的安全场景。');
      insights.push(modelName(precision) + '的精确率最高，说明告警命中率更好，安全人员复核压力更低。');
      if (lgb && hybrid) {
        var recallGain = metricValue(hybrid, 'Recall') - metricValue(lgb, 'Recall');
        var precisionLoss = metricValue(lgb, 'Precision') - metricValue(hybrid, 'Precision');
        insights.push('专家规则融合让召回率变化 ' + pct(recallGain) + '，精确率变化 -' + pct(precisionLoss) + '，体现了“少漏报”和“少误报”之间的取舍。');
      }
      insights.push('孤立森林是无监督基线，不使用标签训练，表现弱于监督模型是符合预期的。');
    }

    var ul = $('experimentInsights');
    ul.innerHTML = '';
    insights.forEach(function(text) {
      var li = document.createElement('li');
      li.textContent = text;
      ul.appendChild(li);
    });
  }

  function renderModelCompare(rows) {
    var tbody = $('modelCompareBody');
    tbody.innerHTML = '';
    if (!rows || rows.length === 0) {
      tbody.innerHTML = '<tr><td colspan="10" style="text-align:center;color:#888;">暂无实验结果</td></tr>';
      if (modelCompareChart) modelCompareChart.clear();
      return;
    }

    rows.forEach(function(row) {
      var cm = confusion(row) || {};
      var tr = document.createElement('tr');
      tr.innerHTML =
        '<td><strong>' + escapeHtml(modelName(row)) + '</strong><div class="model-sub">' + escapeHtml(row.Model || '') + '</div></td>' +
        '<td>' + metricValue(row, 'Accuracy').toFixed(4) + '</td>' +
        '<td>' + metricValue(row, 'Precision').toFixed(4) + '</td>' +
        '<td>' + metricValue(row, 'Recall').toFixed(4) + '</td>' +
        '<td>' + metricValue(row, 'F1-Score').toFixed(4) + '</td>' +
        '<td>' + metricValue(row, 'AUC').toFixed(4) + '</td>' +
        '<td>' + metricValue(row, 'PR-AUC').toFixed(4) + '</td>' +
        '<td>' + metricValue(row, 'HighRisk-Recall').toFixed(4) + '</td>' +
        '<td>' + metricValue(row, 'Security-Score').toFixed(4) + '</td>' +
        '<td>' + (cm.fp !== undefined ? cm.fp : '-') + '</td>' +
        '<td>' + (cm.fn !== undefined ? cm.fn : '-') + '</td>' +
        '<td>' + metricValue(row, 'Latency(ms)').toFixed(4) + '</td>';
      tbody.appendChild(tr);
    });

    if (!modelCompareChart) return;
    var names = rows.map(modelName);
    modelCompareChart.setOption({
      tooltip: {
        trigger: 'axis',
        formatter: function(params) {
          var lines = [params[0].axisValue];
          params.forEach(function(p) { lines.push(p.marker + p.seriesName + '：' + pct(p.value)); });
          return lines.join('<br>');
        },
      },
      legend: { textStyle: { color: '#aaa' } },
      grid: { left: '3%', right: '4%', bottom: '14%', containLabel: true },
      xAxis: { type: 'category', data: names, axisLabel: { color: '#aaa', rotate: 18 } },
      yAxis: { type: 'value', min: 0, max: 1, axisLabel: { color: '#aaa', formatter: function(v) { return (v * 100).toFixed(0) + '%'; } } },
      series: [
        { name: 'F1 综合平衡', type: 'bar', data: rows.map(function(r) { return metricValue(r, 'F1-Score'); }), itemStyle: { color: '#409eff' } },
        { name: 'AUC 区分能力', type: 'bar', data: rows.map(function(r) { return metricValue(r, 'AUC'); }), itemStyle: { color: '#67c23a' } },
        { name: 'PR-AUC 告警质量', type: 'bar', data: rows.map(function(r) { return metricValue(r, 'PR-AUC'); }), itemStyle: { color: '#e6a23c' } },
      ]
    });
  }

  function renderAlertQuality(rows) {
    if (!alertQualityChart || !rows || rows.length === 0) return;
    var names = rows.map(modelName);
    alertQualityChart.setOption({
      tooltip: {
        trigger: 'axis',
        formatter: function(params) {
          var lines = [params[0].axisValue];
          params.forEach(function(p) { lines.push(p.marker + p.seriesName + '：' + pct(p.value)); });
          lines.push('精确率看误报，召回率看漏报。');
          return lines.join('<br>');
        },
      },
      legend: { textStyle: { color: '#aaa' } },
      grid: { left: '3%', right: '4%', bottom: '14%', containLabel: true },
      xAxis: { type: 'category', data: names, axisLabel: { color: '#aaa', rotate: 18 } },
      yAxis: { type: 'value', min: 0, max: 1, axisLabel: { color: '#aaa', formatter: function(v) { return (v * 100).toFixed(0) + '%'; } } },
      series: [
        { name: '精确率：少误报', type: 'bar', data: rows.map(function(r) { return metricValue(r, 'Precision'); }), itemStyle: { color: '#9b59b6' } },
        { name: '召回率：少漏报', type: 'bar', data: rows.map(function(r) { return metricValue(r, 'Recall'); }), itemStyle: { color: '#f56c6c' } },
      ]
    });
  }

  function renderSecurityMetrics(rows) {
    if (!securityMetricChart || !rows || rows.length === 0) return;
    var names = rows.map(modelName);
    securityMetricChart.setOption({
      tooltip: {
        trigger: 'axis',
        formatter: function(params) {
          var lines = [params[0].axisValue];
          params.forEach(function(p) { lines.push(p.marker + p.seriesName + '：' + pct(p.value)); });
          lines.push('安全分基于 5*高危漏报 + 3*普通漏报 + 1*误报，越高越好。');
          return lines.join('<br>');
        },
      },
      legend: { textStyle: { color: '#aaa' } },
      grid: { left: '3%', right: '4%', bottom: '14%', containLabel: true },
      xAxis: { type: 'category', data: names, axisLabel: { color: '#aaa', rotate: 18 } },
      yAxis: { type: 'value', min: 0, max: 1, axisLabel: { color: '#aaa', formatter: function(v) { return (v * 100).toFixed(0) + '%'; } } },
      series: [
        { name: '高危召回：少漏关键风险', type: 'bar', data: rows.map(function(r) { return metricValue(r, 'HighRisk-Recall'); }), itemStyle: { color: '#f56c6c' } },
        { name: '安全代价评分', type: 'bar', data: rows.map(function(r) { return metricValue(r, 'Security-Score'); }), itemStyle: { color: '#409eff' } },
      ]
    });
  }

  function renderConfusionMatrix(rows, selectedModel) {
    var tabs = $('confusionModelTabs');
    var matrix = $('confusionMatrix');
    var note = $('confusionNote');
    if (!tabs || !matrix || !note) return;

    var available = (rows || []).filter(function(row) { return !!confusion(row); });
    tabs.innerHTML = '';

    if (available.length === 0) {
      matrix.innerHTML = '<div class="empty-hint">当前实验结果还没有混淆矩阵。请重新运行 src/evaluation_compare.py 生成新版 experiment_results.json。</div>';
      note.textContent = '混淆矩阵会把正常/异常的正确识别、误报和漏报拆开显示，适合答辩时解释告警效果。';
      return;
    }

    var current = available.find(function(row) { return row.Model === selectedModel; }) || bestOverall(available) || available[0];

    available.forEach(function(row) {
      var btn = document.createElement('button');
      btn.className = 'confusion-tab' + (row.Model === current.Model ? ' active' : '');
      btn.textContent = modelName(row);
      btn.addEventListener('click', function() {
        renderConfusionMatrix(rows, row.Model);
      });
      tabs.appendChild(btn);
    });

    var cm = confusion(current);
    var tn = Number(cm.tn || 0);
    var fp = Number(cm.fp || 0);
    var fn = Number(cm.fn || 0);
    var tp = Number(cm.tp || 0);
    var total = Math.max(tn + fp + fn + tp, 1);
    var falseAlarmRate = fp / Math.max(fp + tn, 1);
    var missRate = fn / Math.max(fn + tp, 1);

    function cell(cls, title, value, desc) {
      return '<div class="confusion-cell ' + cls + '">' +
        '<span class="confusion-title">' + title + '</span>' +
        '<strong>' + value + '</strong>' +
        '<span class="confusion-desc">' + desc + '</span>' +
        '<span class="confusion-share">' + pct(value / total) + '</span>' +
      '</div>';
    }

    matrix.innerHTML =
      '<div class="confusion-axis actual-axis">真实情况</div>' +
      '<div class="confusion-axis pred-axis">系统判断</div>' +
      '<div class="confusion-label top-left"></div>' +
      '<div class="confusion-label pred-normal">判为正常</div>' +
      '<div class="confusion-label pred-alert">判为异常</div>' +
      '<div class="confusion-label actual-normal">真实正常</div>' +
      '<div class="confusion-label actual-alert">真实异常</div>' +
      cell('ok', '正常判正常', tn, '正确放行') +
      cell('warn', '正常误报异常', fp, '误报，需要复核') +
      cell('danger', '异常漏报正常', fn, '漏报，最需关注') +
      cell('ok alert-ok', '异常判异常', tp, '正确告警');

    note.textContent =
      modelName(current) + '：误报率 ' + pct(falseAlarmRate) +
      '，漏报率 ' + pct(missRate) +
      '。答辩时可重点说明系统如何在降低漏报和控制误报之间取舍。';
  }

  function renderFeatureImportance(items) {
    var top = (items || []).slice(0, 10).reverse();
    if (featureImportanceChart) {
      featureImportanceChart.setOption({
        tooltip: {
          trigger: 'axis',
          formatter: function(params) {
            var p = params[0];
            var item = top[p.dataIndex] || {};
            var desc = FEATURE_EXPLAIN[item.feature] || '该特征对模型判断有较高贡献。';
            return escapeHtml(p.name) + '<br>平均贡献强度：' + Number(p.value || 0).toFixed(4) + '<br>' + escapeHtml(desc);
          },
        },
        grid: { left: '4%', right: '6%', top: '6%', bottom: '4%', containLabel: true },
        xAxis: { type: 'value', axisLabel: { color: '#aaa' } },
        yAxis: {
          type: 'category',
          data: top.map(function(i) { return i.feature_cn || i.feature; }),
          axisLabel: { color: '#aaa', fontSize: 10 },
        },
        series: [{
          type: 'bar',
          data: top.map(function(i) { return i.value; }),
          barWidth: 14,
          itemStyle: { color: '#f56c6c' },
        }]
      });
    }

    var notes = $('featureNarrativeList');
    if (!notes) return;
    notes.innerHTML = '';
    (items || []).slice(0, 5).forEach(function(item, idx) {
      var div = document.createElement('div');
      div.className = 'feature-note';
      div.innerHTML =
        '<span class="feature-rank">' + (idx + 1) + '</span>' +
        '<div><strong>' + escapeHtml(item.feature_cn || item.feature) + '</strong>' +
        '<p>' + escapeHtml(FEATURE_EXPLAIN[item.feature] || '该特征对风险评分有明显影响。') + '</p></div>';
      notes.appendChild(div);
    });
  }

  function renderCurves(roc, pr) {
    if (!curveChart) return;
    curveChart.setOption({
      tooltip: {
        trigger: 'axis',
        formatter: function(params) {
          return params.map(function(p) {
            return p.marker + p.seriesName + '：x=' + Number(p.value[0]).toFixed(3) + '，y=' + Number(p.value[1]).toFixed(3);
          }).join('<br>');
        },
      },
      legend: { textStyle: { color: '#aaa' } },
      grid: { left: '5%', right: '5%', top: '10%', bottom: '8%', containLabel: true },
      xAxis: { type: 'value', min: 0, max: 1, name: '误报率 / 召回率', nameTextStyle: { color: '#888' }, axisLabel: { color: '#aaa' } },
      yAxis: { type: 'value', min: 0, max: 1, name: '召回率 / 精确率', nameTextStyle: { color: '#888' }, axisLabel: { color: '#aaa' } },
      series: [
        {
          name: 'ROC：区分能力',
          type: 'line',
          showSymbol: false,
          data: (roc || []).map(function(p) { return [p.x, p.y]; }),
          lineStyle: { color: '#409eff', width: 2 },
        },
        {
          name: 'PR：告警质量',
          type: 'line',
          showSymbol: false,
          data: (pr || []).map(function(p) { return [p.x, p.y]; }),
          lineStyle: { color: '#e6a23c', width: 2 },
        },
      ]
    });
  }

  // ==================== 初始化 ====================
  function init() {
    initTabs();
    initMonitor();
    initTableTab();
    initDetailTab();
    initGraphTab();

    // 窗口尺寸变化重绘图表
    window.addEventListener('resize', function() {
      if (currentTab === 'monitor') resizeCharts();
      if (currentTab === 'experiment') resizeCharts();
      if (currentTab === 'graph' && network) network.fit();
    });

    // 恢复主题
    var savedTheme = localStorage.getItem('uba-theme');
    if (savedTheme === 'light') {
      document.documentElement.classList.add('theme-light');
    }
  }

  // DOM 加载完成后初始化
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
