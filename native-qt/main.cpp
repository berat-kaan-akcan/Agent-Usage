// Agent Usage Widget — Native Cross-Platform Qt6 Desktop Widget (Windows 10/11 & Linux KDE/GNOME/X11/Wayland)
// SPDX-License-Identifier: MIT

#include <QApplication>
#include <QCheckBox>
#include <QCloseEvent>
#include <QComboBox>
#include <QCoreApplication>
#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QFrame>
#include <QHBoxLayout>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLabel>
#include <QMenu>
#include <QMouseEvent>
#include <QProcess>
#include <QProcessEnvironment>
#include <QProgressBar>
#include <QPushButton>
#include <QScrollArea>
#include <QSlider>
#include <QSpinBox>
#include <QStandardPaths>
#include <QStyle>
#include <QSystemTrayIcon>
#include <QTimer>
#include <QVBoxLayout>
#include <QWidget>
#include <QWindow>
#include <iostream>

static QString formatTokens(qint64 count) {
    if (count >= 1000000) {
        return QString::number(static_cast<double>(count) / 1000000.0, 'f', 2) + "M";
    }
    if (count >= 1000) {
        return QString::number(static_cast<double>(count) / 1000.0, 'f', 1) + "K";
    }
    return QString::number(count);
}

class AgentUsageWindow : public QWidget {
    Q_OBJECT

public:
    explicit AgentUsageWindow(bool startMinimized = false, bool selfTest = false, QWidget *parent = nullptr)
        : QWidget(parent), m_startMinimized(startMinimized), m_selfTest(selfTest) {
        setWindowTitle("Agent Usage Widget");
        detectEnvironment();
        loadSettings();
        if (m_startMinimized) {
            m_startMinimizedFlag = true;
        }

        applyWindowFlags();
        resize(m_windowWidth, m_windowHeight);
        if (m_sessionType != "wayland") {
            move(m_windowX, m_windowY);
        }

        buildUi();
        setupTray();
        applyThemeAndOpacity();

        m_refreshProcess = new QProcess(this);
        connect(m_refreshProcess, &QProcess::finished, this, &AgentUsageWindow::onCollectorFinished);

        m_refreshTimer = new QTimer(this);
        connect(m_refreshTimer, &QTimer::timeout, this, &AgentUsageWindow::triggerRefresh);
        m_refreshTimer->start(qMax(15, m_refreshIntervalSec) * 1000);

        triggerRefresh();
    }

protected:
    void mousePressEvent(QMouseEvent *event) override {
        if (event->button() == Qt::LeftButton) {
            if (windowHandle() && windowHandle()->startSystemMove()) {
                event->accept();
                return;
            }
            m_dragging = true;
            m_dragOffset = event->globalPosition().toPoint() - frameGeometry().topLeft();
            event->accept();
        }
        QWidget::mousePressEvent(event);
    }

    void mouseMoveEvent(QMouseEvent *event) override {
        if (m_dragging && (event->buttons() & Qt::LeftButton)) {
            move(event->globalPosition().toPoint() - m_dragOffset);
            event->accept();
        }
        QWidget::mouseMoveEvent(event);
    }

    void mouseReleaseEvent(QMouseEvent *event) override {
        if (event->button() == Qt::LeftButton) {
            m_dragging = false;
            saveSettings();
        }
        QWidget::mouseReleaseEvent(event);
    }

    void closeEvent(QCloseEvent *event) override {
        saveSettings();
        if (m_minimizeToTray && m_trayIcon && m_trayIcon->isVisible()) {
            event->ignore();
            hide();
            return;
        }
        event->accept();
        QApplication::quit();
    }

private slots:
    void triggerRefresh() {
        if (m_refreshProcess->state() != QProcess::NotRunning) {
            return;
        }
        m_refreshBtn->setEnabled(false);
        m_subtitleLabel->setText("Refreshing usage & quotas...");

        QProcessEnvironment env = QProcessEnvironment::systemEnvironment();
        QString repoSrc = findPythonPackageSrc();
        if (!repoSrc.isEmpty()) {
            QString existing = env.value("PYTHONPATH");
            env.insert("PYTHONPATH", existing.isEmpty() ? repoSrc : (repoSrc + ":" + existing));
        }
        m_refreshProcess->setProcessEnvironment(env);

        QStringList args;
        args << "-m" << "agent_usage.cli" << "--json" << "--period" << m_selectedPeriod;
        if (!m_networkQuotaEnabled) {
            args << "--no-network";
        }
        m_refreshProcess->start("python3", args);
    }

    void onCollectorFinished(int exitCode, QProcess::ExitStatus status) {
        m_refreshBtn->setEnabled(true);
        if (status == QProcess::NormalExit && exitCode == 0) {
            QByteArray raw = m_refreshProcess->readAllStandardOutput();
            QJsonParseError err;
            QJsonDocument doc = QJsonDocument::fromJson(raw, &err);
            if (err.error == QJsonParseError::NoError && doc.isObject()) {
                m_latestData = doc.object();
                m_subtitleLabel->setText("Last updated: " + QDateTime::currentDateTime().toString("HH:mm:ss"));
                renderCards();
            } else {
                m_subtitleLabel->setText("Parse warning: " + err.errorString());
            }
        } else {
            m_subtitleLabel->setText("Collector exited with code " + QString::number(exitCode));
        }

        if (m_selfTest) {
            std::cout << "Native Qt6 Agent Usage Widget self-test completed! Providers loaded: "
                      << m_latestData.keys().size() << std::endl;
            saveSettings();
            QTimer::singleShot(50, qApp, &QApplication::quit);
        }
    }

    void toggleViewMode() {
        if (m_viewMode == "expanded") {
            m_viewMode = "compact";
            m_viewBtn->setText("Expand");
            resize(width(), 320);
        } else {
            m_viewMode = "expanded";
            m_viewBtn->setText("Compact");
            resize(width(), qMax(560, m_windowHeight));
        }
        saveSettings();
        renderCards();
    }

    void toggleSettingsPanel(bool checked) {
        m_settingsFrame->setVisible(checked);
    }

private:
    void detectEnvironment() {
#if defined(Q_OS_WIN)
        m_desktopEnv = "Windows Explorer";
        m_sessionType = "windows";
        m_aotNote = "Supported natively via Win32 HWND_TOPMOST.";
#else
        QString xdgDesktop = qEnvironmentVariable("XDG_CURRENT_DESKTOP", "Linux Desktop");
        m_desktopEnv = xdgDesktop;
        m_sessionType = qEnvironmentVariable("XDG_SESSION_TYPE", "").toLower();
        if (m_sessionType.isEmpty()) {
            m_sessionType = !qEnvironmentVariable("WAYLAND_DISPLAY").isEmpty() ? "wayland" : "x11";
        }
        if (m_sessionType == "wayland") {
            m_aotNote = "Wayland session (" + m_desktopEnv + "): WindowStaysOnTopHint is requested; "
                        "compositor rules (e.g. KDE KWin 'Keep Above Others') control global stacking.";
        } else {
            m_aotNote = "X11 session (" + m_desktopEnv + "): Native _NET_WM_STATE_ABOVE supported.";
        }
#endif
    }

    QString settingsFilePath() const {
        QString overrideDir = qEnvironmentVariable("AGENT_USAGE_CONFIG_DIR");
        if (!overrideDir.isEmpty()) {
            return QDir(overrideDir).filePath("settings.json");
        }
        QString configHome = qEnvironmentVariable("XDG_CONFIG_HOME");
        if (configHome.isEmpty()) {
            configHome = QDir::home().filePath(".config");
        }
        return QDir(configHome).filePath("agent-usage-widget/settings.json");
    }

    QString findPythonPackageSrc() const {
        QDir appDir(QCoreApplication::applicationDirPath());
        QStringList candidates = {
            appDir.filePath("../src"),
            appDir.filePath("src"),
            QDir::current().filePath("src"),
            "/usr/lib/agent-usage-widget/src"
        };
        for (const QString &c : candidates) {
            if (QDir(c).exists("agent_usage")) {
                return QFileInfo(c).absoluteFilePath();
            }
        }
        return QString();
    }

    void loadSettings() {
        QFile f(settingsFilePath());
        if (!f.open(QIODevice::ReadOnly)) {
            return;
        }
        QJsonDocument doc = QJsonDocument::fromJson(f.readAll());
        if (!doc.isObject()) {
            return;
        }
        QJsonObject obj = doc.object();
        m_viewMode = obj.value("view_mode").toString("expanded");
        m_theme = obj.value("theme").toString("dark");
        m_alwaysOnTop = obj.value("always_on_top").toBool(true);
        m_opacity = qBound(0.35, obj.value("opacity").toDouble(0.95), 1.0);
        m_refreshIntervalSec = qBound(15, obj.value("refresh_interval_seconds").toInt(60), 3600);
        m_minimizeToTray = obj.value("minimize_to_tray").toBool(true);
        m_startMinimizedFlag = obj.value("start_minimized").toBool(false);
        m_startWithSystem = obj.value("start_with_system").toBool(false);
        m_networkQuotaEnabled = obj.value("network_quota_enabled").toBool(true);
        m_windowX = obj.value("window_x").toInt(80);
        m_windowY = obj.value("window_y").toInt(80);
        m_windowWidth = qBound(280, obj.value("window_width").toInt(400), 1600);
        m_windowHeight = qBound(180, obj.value("window_height").toInt(620), 1600);
        m_selectedPeriod = obj.value("selected_time_period").toString("seven_day");
    }

    void saveSettings() {
        if (m_viewMode == "expanded" && isVisible()) {
            m_windowWidth = width();
            m_windowHeight = height();
        }
        if (isVisible()) {
            m_windowX = x();
            m_windowY = y();
        }
        QJsonObject obj;
        obj["view_mode"] = m_viewMode;
        obj["theme"] = m_theme;
        obj["always_on_top"] = m_alwaysOnTop;
        obj["opacity"] = m_opacity;
        obj["refresh_interval_seconds"] = m_refreshIntervalSec;
        obj["minimize_to_tray"] = m_minimizeToTray;
        obj["start_minimized"] = m_startMinimizedFlag;
        obj["start_with_system"] = m_startWithSystem;
        obj["network_quota_enabled"] = m_networkQuotaEnabled;
        obj["window_x"] = m_windowX;
        obj["window_y"] = m_windowY;
        obj["window_width"] = m_windowWidth;
        obj["window_height"] = m_windowHeight;
        obj["selected_time_period"] = m_selectedPeriod;
        QJsonArray provs = {"claude", "codex", "antigravity"};
        obj["enabled_providers"] = provs;

        QFileInfo fi(settingsFilePath());
        QDir().mkpath(fi.absolutePath());
        QFile f(fi.absoluteFilePath());
        if (f.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
            f.write(QJsonDocument(obj).toJson(QJsonDocument::Indented));
        }
    }

    void applyWindowFlags() {
        Qt::WindowFlags flags = Qt::Window;
        if (m_alwaysOnTop) {
            flags |= Qt::WindowStaysOnTopHint;
        }
        setWindowFlags(flags);
    }

    void buildUi() {
        auto *mainLayout = new QVBoxLayout(this);
        mainLayout->setContentsMargins(12, 10, 12, 10);
        mainLayout->setSpacing(8);

        // Header
        auto *header = new QHBoxLayout();
        auto *titleCol = new QVBoxLayout();
        m_titleLabel = new QLabel("AI Agent Usage");
        m_titleLabel->setObjectName("widgetTitle");
        m_subtitleLabel = new QLabel("Initializing...");
        m_subtitleLabel->setObjectName("mutedText");
        titleCol->addWidget(m_titleLabel);
        titleCol->addWidget(m_subtitleLabel);
        header->addLayout(titleCol, 1);

        m_periodCombo = new QComboBox();
        m_periodCombo->addItem("5h Window", "five_hour");
        m_periodCombo->addItem("Today", "today");
        m_periodCombo->addItem("7 Days", "seven_day");
        m_periodCombo->addItem("30 Days", "thirty_day");
        m_periodCombo->addItem("All Time", "all_time");
        int pIdx = m_periodCombo->findData(m_selectedPeriod);
        if (pIdx >= 0) m_periodCombo->setCurrentIndex(pIdx);
        connect(m_periodCombo, &QComboBox::currentIndexChanged, this, [this](int) {
            m_selectedPeriod = m_periodCombo->currentData().toString();
            saveSettings();
            renderCards();
        });
        header->addWidget(m_periodCombo);

        m_viewBtn = new QPushButton(m_viewMode == "expanded" ? "Compact" : "Expand");
        connect(m_viewBtn, &QPushButton::clicked, this, &AgentUsageWindow::toggleViewMode);
        header->addWidget(m_viewBtn);

        m_refreshBtn = new QPushButton("Refresh");
        connect(m_refreshBtn, &QPushButton::clicked, this, &AgentUsageWindow::triggerRefresh);
        header->addWidget(m_refreshBtn);

        m_settingsBtn = new QPushButton("⚙");
        m_settingsBtn->setCheckable(true);
        connect(m_settingsBtn, &QPushButton::toggled, this, &AgentUsageWindow::toggleSettingsPanel);
        header->addWidget(m_settingsBtn);

        mainLayout->addLayout(header);

        // Collapsible Settings Panel
        m_settingsFrame = new QFrame();
        m_settingsFrame->setObjectName("providerCard");
        m_settingsFrame->setVisible(false);
        buildSettingsPanel();
        mainLayout->addWidget(m_settingsFrame);

        // Scroll Area
        auto *scroll = new QScrollArea();
        scroll->setWidgetResizable(true);
        scroll->setFrameShape(QFrame::NoFrame);
        auto *container = new QWidget();
        m_cardsLayout = new QVBoxLayout(container);
        m_cardsLayout->setContentsMargins(0, 0, 0, 0);
        m_cardsLayout->setSpacing(10);
        scroll->setWidget(container);
        mainLayout->addWidget(scroll, 1);

        // Footer
        auto *footer = new QLabel(m_desktopEnv + " (" + m_sessionType.toUpper() + ")");
        footer->setObjectName("mutedText");
        footer->setWordWrap(true);
        mainLayout->addWidget(footer);
    }

    void buildSettingsPanel() {
        auto *slayout = new QVBoxLayout(m_settingsFrame);
        slayout->setSpacing(6);

        auto *row1 = new QHBoxLayout();
        row1->addWidget(new QLabel("Theme:"));
        auto *themeCombo = new QComboBox();
        themeCombo->addItem("Dark Theme", "dark");
        themeCombo->addItem("Light Theme", "light");
        int tIdx = themeCombo->findData(m_theme);
        if (tIdx >= 0) themeCombo->setCurrentIndex(tIdx);
        connect(themeCombo, &QComboBox::currentIndexChanged, this, [this, themeCombo](int) {
            m_theme = themeCombo->currentData().toString();
            saveSettings();
            applyThemeAndOpacity();
        });
        row1->addWidget(themeCombo);
        slayout->addLayout(row1);

        auto *row2 = new QHBoxLayout();
        row2->addWidget(new QLabel("Opacity:"));
        auto *opSlider = new QSlider(Qt::Horizontal);
        opSlider->setRange(40, 100);
        opSlider->setValue(static_cast<int>(m_opacity * 100));
        connect(opSlider, &QSlider::valueChanged, this, [this](int val) {
            m_opacity = val / 100.0;
            setWindowOpacity(m_opacity);
            saveSettings();
        });
        row2->addWidget(opSlider);
        slayout->addLayout(row2);

        auto *row3 = new QHBoxLayout();
        row3->addWidget(new QLabel("Refresh Interval (sec):"));
        auto *spin = new QSpinBox();
        spin->setRange(15, 3600);
        spin->setSingleStep(15);
        spin->setValue(m_refreshIntervalSec);
        connect(spin, &QSpinBox::valueChanged, this, [this](int val) {
            m_refreshIntervalSec = val;
            m_refreshTimer->start(qMax(15, val) * 1000);
            saveSettings();
        });
        row3->addWidget(spin);
        slayout->addLayout(row3);

        auto *aotCb = new QCheckBox("Always on Top");
        aotCb->setChecked(m_alwaysOnTop);
        aotCb->setToolTip(m_aotNote);
        connect(aotCb, &QCheckBox::toggled, this, [this](bool checked) {
            m_alwaysOnTop = checked;
            saveSettings();
            bool vis = isVisible();
            applyWindowFlags();
            if (vis) show();
        });
        slayout->addWidget(aotCb);

        auto *trayCb = new QCheckBox("Minimize to System Tray on Close");
        trayCb->setChecked(m_minimizeToTray);
        connect(trayCb, &QCheckBox::toggled, this, [this](bool checked) {
            m_minimizeToTray = checked;
            saveSettings();
        });
        slayout->addWidget(trayCb);

        auto *netCb = new QCheckBox("Poll Live Account Quota APIs");
        netCb->setChecked(m_networkQuotaEnabled);
        connect(netCb, &QCheckBox::toggled, this, [this](bool checked) {
            m_networkQuotaEnabled = checked;
            saveSettings();
            triggerRefresh();
        });
        slayout->addWidget(netCb);

        auto *noteLbl = new QLabel(m_aotNote);
        noteLbl->setObjectName("mutedText");
        noteLbl->setWordWrap(true);
        slayout->addWidget(noteLbl);
    }

    void setupTray() {
        if (!QSystemTrayIcon::isSystemTrayAvailable()) {
            return;
        }
        m_trayIcon = new QSystemTrayIcon(this);
        m_trayIcon->setIcon(style()->standardIcon(QStyle::SP_ComputerIcon));
        m_trayIcon->setToolTip("Agent Usage Widget");

        auto *menu = new QMenu(this);
        menu->addAction("Show / Hide Widget", this, [this]() {
            if (isVisible()) hide();
            else { showNormal(); raise(); activateWindow(); }
        });
        menu->addAction("Refresh Usage Now", this, &AgentUsageWindow::triggerRefresh);
        menu->addAction("Toggle Compact / Expanded", this, &AgentUsageWindow::toggleViewMode);
        menu->addSeparator();
        menu->addAction("Quit Agent Usage", this, [this]() {
            saveSettings();
            QApplication::quit();
        });
        m_trayIcon->setContextMenu(menu);
        connect(m_trayIcon, &QSystemTrayIcon::activated, this, [this](QSystemTrayIcon::ActivationReason r) {
            if (r == QSystemTrayIcon::Trigger) {
                if (isVisible()) hide();
                else { showNormal(); raise(); activateWindow(); }
            }
        });
        m_trayIcon->show();
    }

    void applyThemeAndOpacity() {
        bool dark = (m_theme != "light");
        QString bgPrimary = dark ? "#0F141C" : "#F8FAFC";
        QString bgCard = dark ? "#18202C" : "#FFFFFF";
        QString textPrimary = dark ? "#F1F5F9" : "#0F172A";
        QString textSecondary = dark ? "#CBD5E1" : "#334155";
        QString textMuted = dark ? "#94A3B8" : "#64748B";
        QString border = dark ? "#2A364B" : "#CBD5E1";
        QString progressBg = dark ? "#263246" : "#E2E8F0";

        QString qss = QString(
            "QWidget { background-color: %1; color: %2; font-family: sans-serif; }"
            "#widgetTitle { font-size: 15px; font-weight: bold; color: %2; }"
            "#providerCard { background-color: %3; border: 1px solid %6; border-radius: 8px; padding: 8px; }"
            "#providerHeader { font-size: 14px; font-weight: bold; color: %2; background: transparent; }"
            "#secondaryText { font-size: 12px; color: %4; background: transparent; }"
            "#mutedText { font-size: 11px; color: %5; background: transparent; }"
            "#badgeOk { color: #22C55E; font-weight: bold; font-size: 11px; background: transparent; }"
            "#badgeWarn { color: #F59E0B; font-weight: bold; font-size: 11px; background: transparent; }"
            "#badgeErr { color: #EF4444; font-weight: bold; font-size: 11px; background: transparent; }"
            "QPushButton, QComboBox, QSpinBox { background-color: %3; border: 1px solid %6; border-radius: 4px; padding: 4px 8px; color: %2; }"
            "QProgressBar { border: 1px solid %6; border-radius: 4px; background-color: %7; text-align: center; height: 12px; font-size: 10px; }"
            "QProgressBar::chunk { background-color: #4285F4; border-radius: 3px; }"
        ).arg(bgPrimary, textPrimary, bgCard, textSecondary, textMuted, border, progressBg);

        setStyleSheet(qss);
        setWindowOpacity(m_opacity);
    }

    void renderCards() {
        QLayoutItem *item;
        while ((item = m_cardsLayout->takeAt(0)) != nullptr) {
            if (QWidget *w = item->widget()) {
                w->deleteLater();
            }
            delete item;
        }

        bool isCompact = (m_viewMode == "compact");
        QStringList order = {"claude", "codex", "antigravity"};
        for (const QString &pid : order) {
            if (!m_latestData.contains(pid)) continue;
            QJsonObject snap = m_latestData.value(pid).toObject();
            m_cardsLayout->addWidget(buildProviderCard(snap, isCompact));
        }
        m_cardsLayout->addStretch(1);
    }

    QWidget *buildProviderCard(const QJsonObject &snap, bool isCompact) {
        auto *card = new QFrame();
        card->setObjectName("providerCard");
        auto *clayout = new QVBoxLayout(card);
        clayout->setSpacing(5);

        auto *topRow = new QHBoxLayout();
        auto *nameLbl = new QLabel(snap.value("display_name").toString());
        nameLbl->setObjectName("providerHeader");
        topRow->addWidget(nameLbl, 1);

        QString authState = snap.value("auth_state").toString().toUpper();
        bool isStale = snap.value("is_stale").toBool(false);
        QString badgeText = isStale ? (authState + " • STALE") : authState;
        auto *badgeLbl = new QLabel(badgeText);
        if (authState == "AUTHENTICATED" && !isStale) {
            badgeLbl->setObjectName("badgeOk");
        } else if (authState == "LOCAL_ONLY" || authState == "RATE_LIMITED" || isStale) {
            badgeLbl->setObjectName("badgeWarn");
        } else {
            badgeLbl->setObjectName("badgeErr");
        }
        topRow->addWidget(badgeLbl);
        clayout->addLayout(topRow);

        QString planName = snap.value("plan_name").toString();
        QString creditsLabel = snap.value("credits_label").toString();
        if (!planName.isEmpty() || !creditsLabel.isEmpty()) {
            QStringList parts;
            if (!planName.isEmpty()) parts << planName;
            if (!creditsLabel.isEmpty()) parts << creditsLabel;
            auto *metaLbl = new QLabel(parts.join(" • "));
            metaLbl->setObjectName("secondaryText");
            clayout->addWidget(metaLbl);
        }

        QString usageKey = "usage_" + m_selectedPeriod;
        QJsonObject usageObj = snap.value(usageKey).toObject();
        qint64 totalTok = usageObj.value("total_tokens").toInteger();
        qint64 inTok = usageObj.value("input_tokens").toInteger();
        qint64 cacheTok = usageObj.value("cache_read_tokens").toInteger() + usageObj.value("cache_creation_tokens").toInteger();
        qint64 outTok = usageObj.value("output_tokens").toInteger();
        int sessCount = usageObj.value("session_count").toInt();

        auto *tokLbl = new QLabel(QString("Local Tokens (%1): %2 total (In: %3 | Cache: %4 | Out: %5) • %6 sess")
            .arg(m_selectedPeriod, formatTokens(totalTok), formatTokens(inTok), formatTokens(cacheTok), formatTokens(outTok))
            .arg(sessCount));
        tokLbl->setObjectName("secondaryText");
        tokLbl->setWordWrap(true);
        clayout->addWidget(tokLbl);

        QJsonArray qwindows = snap.value("quota_windows").toArray();
        int limit = isCompact ? qMin(2, static_cast<int>(qwindows.size())) : static_cast<int>(qwindows.size());
        for (int i = 0; i < limit; ++i) {
            QJsonObject qw = qwindows.at(i).toObject();
            auto *qrow = new QHBoxLayout();
            auto *qlbl = new QLabel(qw.value("label").toString());
            qlbl->setObjectName("mutedText");
            qrow->addWidget(qlbl, 1);

            double usedPct = qw.value("used_percent").toDouble();
            double remPct = qw.value("remaining_percent").toDouble();
            QString countdown = qw.value("reset_countdown").toString();
            QString rtext = QString("%1% used (%2% left)").arg(usedPct, 0, 'f', 1).arg(remPct, 0, 'f', 1);
            if (!countdown.isEmpty()) {
                rtext += " • resets " + countdown;
            }
            auto *rlbl = new QLabel(rtext);
            rlbl->setObjectName("mutedText");
            qrow->addWidget(rlbl);
            clayout->addLayout(qrow);

            auto *pbar = new QProgressBar();
            pbar->setRange(0, 100);
            pbar->setValue(qBound(0, static_cast<int>(qRound(usedPct)), 100));
            clayout->addWidget(pbar);
        }

        if (!isCompact) {
            QJsonObject modelUsage = snap.value("model_usage_all_time").toObject();
            if (!modelUsage.isEmpty()) {
                auto *mh = new QLabel("Recorded Local Usage by Model (All-Time):");
                mh->setObjectName("mutedText");
                clayout->addWidget(mh);
                for (auto it = modelUsage.begin(); it != modelUsage.end(); ++it) {
                    QJsonObject ms = it.value().toObject();
                    auto *ml = new QLabel(QString("  • %1: %2 (In %3, Out %4)")
                        .arg(it.key(),
                             formatTokens(ms.value("total_tokens").toInteger()),
                             formatTokens(ms.value("input_tokens").toInteger()),
                             formatTokens(ms.value("output_tokens").toInteger())));
                    ml->setObjectName("mutedText");
                    clayout->addWidget(ml);
                }
            }

            auto *statusLbl = new QLabel(snap.value("status_message").toString());
            statusLbl->setObjectName("mutedText");
            statusLbl->setWordWrap(true);
            clayout->addWidget(statusLbl);

            auto *scopeLbl = new QLabel(snap.value("scope_note").toString());
            scopeLbl->setObjectName("mutedText");
            scopeLbl->setWordWrap(true);
            clayout->addWidget(scopeLbl);
        }

        return card;
    }

    bool m_startMinimized = false;
    bool m_selfTest = false;
    bool m_dragging = false;
    QPoint m_dragOffset;

    QString m_desktopEnv;
    QString m_sessionType;
    QString m_aotNote;

    QString m_viewMode = "expanded";
    QString m_theme = "dark";
    bool m_alwaysOnTop = true;
    double m_opacity = 0.95;
    int m_refreshIntervalSec = 60;
    bool m_minimizeToTray = true;
    bool m_startMinimizedFlag = false;
    bool m_startWithSystem = false;
    bool m_networkQuotaEnabled = true;
    int m_windowX = 80;
    int m_windowY = 80;
    int m_windowWidth = 420;
    int m_windowHeight = 640;
    QString m_selectedPeriod = "seven_day";

    QJsonObject m_latestData;

    QLabel *m_titleLabel = nullptr;
    QLabel *m_subtitleLabel = nullptr;
    QComboBox *m_periodCombo = nullptr;
    QPushButton *m_viewBtn = nullptr;
    QPushButton *m_refreshBtn = nullptr;
    QPushButton *m_settingsBtn = nullptr;
    QFrame *m_settingsFrame = nullptr;
    QVBoxLayout *m_cardsLayout = nullptr;
    QSystemTrayIcon *m_trayIcon = nullptr;
    QProcess *m_refreshProcess = nullptr;
    QTimer *m_refreshTimer = nullptr;
};

#include "main.moc"

int main(int argc, char *argv[]) {
    bool minimized = false;
    bool selfTest = false;
    for (int i = 1; i < argc; ++i) {
        QString arg = QString::fromUtf8(argv[i]);
        if (arg == "--minimized") minimized = true;
        if (arg == "--self-test") selfTest = true;
    }
    if (selfTest && qEnvironmentVariableIsEmpty("QT_QPA_PLATFORM")) {
        qputenv("QT_QPA_PLATFORM", "offscreen");
    }

    QApplication app(argc, argv);
    app.setApplicationName("agent-usage-widget");
    app.setApplicationVersion("1.0.0");

    AgentUsageWindow win(minimized, selfTest);
    if (!minimized) {
        win.show();
    }
    return app.exec();
}
