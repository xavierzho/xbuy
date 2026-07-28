#!/usr/bin/env python3
"""
PyQt6 桌面端抢购控制台
- 按 ui.html 主要布局实现（左侧账号 + 右侧标签页）
- 按钮直接调用核心源码（accounts.py / app.py）
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from product_catalog import load_catalog_items
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QComboBox,
)

from accounts import POOL, STATUS_OK
from shield import (
    make_raw_id,
    make_device_token,
    make_h5_devid,
    make_h5_session,
    make_kv_pair,
    seal_param,
    unseal_param,
    shield_for_post,
    decrypt_shield,
)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("i茅台抢购控制台 · PyQt6")
        self.resize(1360, 860)

        self.selected_phone: str | None = None
        self.selected_addr: dict | None = None
        self.selected_item: dict | None = None
        self.current_addresses: list[dict] = []
        self.all_items: list[dict] = []

        self._build_ui()
        self.load_accounts()
        self.load_items()

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        main_layout = QVBoxLayout(root)

        topbar = QHBoxLayout()
        self.lbl_title = QLabel("i茅台抢购控制台 (桌面版)")
        self.lbl_stats = QLabel("账号总数 0 | 已登录 0 | 已发码 0 | 失败 0")
        self.lbl_stats.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.btn_nodes = QPushButton("节点 0")
        self.btn_nodes.setToolTip("查看分布式节点状态")
        self.btn_nodes.clicked.connect(self.show_distributed_nodes)
        self.btn_top_batch_rush = QPushButton("一键抢购")
        self.btn_top_batch_rush.setToolTip("顶栏快捷入口：当前桌面壳先记录操作，批量流式逻辑待接入")
        self.btn_top_batch_rush.clicked.connect(lambda: self.stub_log("[顶栏] 一键抢购：待接入批量流式抢购"))
        topbar.addWidget(self.lbl_title, 1)
        topbar.addWidget(self.lbl_stats, 2)
        topbar.addWidget(self.btn_nodes)
        topbar.addWidget(self.btn_top_batch_rush)
        main_layout.addLayout(topbar)

        body = QHBoxLayout()
        main_layout.addLayout(body, 1)

        # 左侧账号区域
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left.setMinimumWidth(320)
        left.setMaximumWidth(380)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索手机号/昵称…")
        self.search_input.textChanged.connect(self.render_accounts)
        left_layout.addWidget(self.search_input)

        toolbar = QHBoxLayout()
        self.btn_add = QPushButton("+ 添加")
        self.btn_batch_import = QPushButton("批量导入")
        self.btn_batch_send = QPushButton("批量发码")
        self.btn_refresh_login_all = QPushButton("全部刷新登录")
        self.btn_refresh_login_all.setToolTip(
            "校验会话：按 JWT 对齐设备并发收货地址请求（默认不换机模，避免 4011）"
        )
        self.btn_add.clicked.connect(self.add_account)
        self.btn_batch_import.clicked.connect(self.batch_import)
        self.btn_batch_send.clicked.connect(self.batch_send_code)
        self.btn_refresh_login_all.clicked.connect(self.batch_refresh_login_all)
        toolbar.addWidget(self.btn_add)
        toolbar.addWidget(self.btn_batch_import)
        toolbar.addWidget(self.btn_batch_send)
        toolbar.addWidget(self.btn_refresh_login_all)
        left_layout.addLayout(toolbar)

        group_tabs = QHBoxLayout()
        self.group_buttons: list[QPushButton] = []
        for name in ("全部", "白号", "黑号", "抢购成功"):
            btn = QPushButton(f"{name} 0")
            btn.setToolTip(f"账号分组：{name}")
            btn.clicked.connect(lambda _, n=name: self.stub_log(f"[账号分组] 切换到 {n}（待接入筛选）"))
            self.group_buttons.append(btn)
            group_tabs.addWidget(btn)
        left_layout.addLayout(group_tabs)

        group_ops = QHBoxLayout()
        for name in ("＋ 新建分组", "移入分组", "移出分组"):
            btn = QPushButton(name)
            btn.clicked.connect(lambda _, n=name: self.stub_log(f"[账号分组] {n}：待接入"))
            group_ops.addWidget(btn)
        left_layout.addLayout(group_ops)

        self.acct_list = QListWidget()
        self.acct_list.itemSelectionChanged.connect(self.on_select_account)
        left_layout.addWidget(self.acct_list, 1)
        body.addWidget(left)

        # 右侧主内容
        right = QWidget()
        right_layout = QVBoxLayout(right)
        self.tabs = QTabWidget()
        right_layout.addWidget(self.tabs, 1)
        body.addWidget(right, 1)

        self._build_tab_account()
        self._build_tab_rush()
        self._build_tab_items()
        self._build_tab_log()
        self._build_tab_pay()
        self._build_tab_shield()

    def _build_tab_account(self):
        tab = QWidget()
        outer = QVBoxLayout(tab)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        global_box = QGroupBox("全局开关")
        global_row = QVBoxLayout(global_box)
        self.chk_auto_chain = QCheckBox("抢购成功后自动组单、下单并拉支付链接；支付成功在「支付通知」页展示")
        self.chk_show_device_refresh = QCheckBox("显示「刷新设备参数」入口（换新指纹并清除登录态，需要重新发码登录）")
        global_row.addWidget(self.chk_auto_chain)
        global_row.addWidget(self.chk_show_device_refresh)
        layout.addWidget(global_box)

        self._build_batch_config_panel(layout)

        acct_box = QGroupBox("当前账号配置")
        acct_layout = QVBoxLayout(acct_box)
        self.detail_info = QTextEdit()
        self.detail_info.setReadOnly(True)
        self.detail_info.setMaximumHeight(150)
        acct_layout.addWidget(self.detail_info)

        row = QHBoxLayout()
        self.btn_send_code = QPushButton("发送验证码")
        self.btn_login = QPushButton("登录")
        self.btn_load_addr = QPushButton("查询地址")
        self.btn_remove = QPushButton("删除账号")
        self.btn_send_code.clicked.connect(self.send_code_for_selected)
        self.btn_login.clicked.connect(self.login_selected)
        self.btn_load_addr.clicked.connect(self.load_addr_for_selected)
        self.btn_remove.clicked.connect(self.remove_selected)
        row.addWidget(self.btn_send_code)
        row.addWidget(self.btn_login)
        row.addWidget(self.btn_load_addr)
        row.addStretch(1)
        row.addWidget(self.btn_remove)
        acct_layout.addLayout(row)

        self.addr_box = QTextEdit()
        self.addr_box.setReadOnly(True)
        self.addr_box.setPlaceholderText("地址列表")
        self.addr_box.setMaximumHeight(160)
        acct_layout.addWidget(self.addr_box)
        layout.addWidget(acct_box)
        layout.addStretch(1)

        self.tabs.addTab(tab, "抢购配置")

    def _build_batch_config_panel(self, parent_layout: QVBoxLayout):
        box = QGroupBox("批量与定时（北京时间）")
        layout = QVBoxLayout(box)

        self.chk_global_force_local = QCheckBox("本机IP（全局）：勾选后所有账号本机出口；取消后走代理池/分片")
        layout.addWidget(self.chk_global_force_local)

        sched = QHBoxLayout()
        self.btn_sched_date = QPushButton("选择日期…")
        self.in_sched_hour = QSpinBox()
        self.in_sched_hour.setRange(0, 23)
        self.in_sched_minute = QSpinBox()
        self.in_sched_minute.setRange(0, 59)
        self.in_sched_second = QSpinBox()
        self.in_sched_second.setRange(0, 59)
        for w in (self.in_sched_hour, self.in_sched_minute, self.in_sched_second):
            w.setFixedWidth(64)
        sched.addWidget(QLabel("定时开抢日期"))
        sched.addWidget(self.btn_sched_date)
        sched.addWidget(QLabel("时间"))
        sched.addWidget(self.in_sched_hour)
        sched.addWidget(QLabel(":"))
        sched.addWidget(self.in_sched_minute)
        sched.addWidget(QLabel(":"))
        sched.addWidget(self.in_sched_second)
        for text in ("启用定时批量抢购", "取消定时"):
            btn = QPushButton(text)
            btn.clicked.connect(lambda _, t=text: self.stub_log(f"[定时] {t}：待接入"))
            sched.addWidget(btn)
        sched.addStretch(1)
        layout.addLayout(sched)

        self.chk_sched_all = QCheckBox("定时每轮：全部已登录账号均参与批量抢购")
        self.chk_sched_all.setChecked(True)
        self.chk_sched_prewarm = QCheckBox("开抢前自动 HTTP 预热")
        self.chk_sched_prewarm.setChecked(True)
        layout.addWidget(self.chk_sched_all)
        layout.addWidget(self.chk_sched_prewarm)

        prewarm = QHBoxLayout()
        self.in_sched_prewarm_lead = QSpinBox()
        self.in_sched_prewarm_lead.setRange(5, 180)
        self.in_sched_prewarm_lead.setValue(14)
        self.in_prewarm_parallel = QSpinBox()
        self.in_prewarm_parallel.setRange(1, 24)
        self.in_prewarm_parallel.setValue(6)
        prewarm.addWidget(QLabel("提前(秒)"))
        prewarm.addWidget(self.in_sched_prewarm_lead)
        prewarm.addWidget(QLabel("预热并行账号"))
        prewarm.addWidget(self.in_prewarm_parallel)
        prewarm.addWidget(QLabel("定时状态：未开启（页面需保持打开）"))
        prewarm.addStretch(1)
        layout.addLayout(prewarm)

        row1 = QHBoxLayout()
        self.in_batch_default_amt = QSpinBox()
        self.in_batch_default_amt.setRange(1, 99)
        self.in_batch_default_amt.setValue(2)
        self.in_batch_retry = QSpinBox()
        self.in_batch_retry.setRange(1, 200)
        self.in_batch_retry.setValue(10)
        self.in_batch_delay = QSpinBox()
        self.in_batch_delay.setRange(0, 120000)
        self.in_batch_delay.setValue(500)
        self.in_batch_concurrency = QSpinBox()
        self.in_batch_concurrency.setRange(1, 2048)
        self.in_batch_concurrency.setValue(12)
        for label, widget in (
            ("批量默认数量", self.in_batch_default_amt),
            ("重试次数", self.in_batch_retry),
            ("间隔毫秒", self.in_batch_delay),
            ("批量并发数", self.in_batch_concurrency),
        ):
            row1.addWidget(QLabel(label))
            row1.addWidget(widget)
        btn_apply_all = QPushButton("一键配置全部已登录账号")
        btn_apply_all.clicked.connect(lambda: self.stub_log("[批量] 一键配置全部已登录账号：待接入"))
        row1.addWidget(btn_apply_all)
        row1.addStretch(1)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        self.chk_distributed_rush = QCheckBox("分布式节点")
        self.chk_batch_mt_mode = QCheckBox("MT模式（Node rush_loop，每枪一发一显）")
        self.chk_batch_fire = QCheckBox("高频（不等响应连发）")
        self.in_batch_rounds = QLineEdit()
        self.in_batch_rounds.setPlaceholderText("同抢购页")
        self.in_batch_round_gap = QLineEdit()
        self.in_batch_round_gap.setPlaceholderText("ms")
        self.in_batch_round_jitter = QLineEdit()
        self.in_batch_round_jitter.setPlaceholderText("ms")
        for w in (self.in_batch_rounds, self.in_batch_round_gap, self.in_batch_round_jitter):
            w.setMaximumWidth(110)
        row2.addWidget(self.chk_distributed_rush)
        row2.addWidget(self.chk_batch_mt_mode)
        row2.addWidget(self.chk_batch_fire)
        row2.addWidget(QLabel("批量·轮次"))
        row2.addWidget(self.in_batch_rounds)
        row2.addWidget(QLabel("轮次间隔"))
        row2.addWidget(self.in_batch_round_gap)
        row2.addWidget(QLabel("抖动±"))
        row2.addWidget(self.in_batch_round_jitter)
        row2.addStretch(1)
        layout.addLayout(row2)

        self._build_proxy_pool_panel(layout)

        black = QHBoxLayout()
        self.in_black_retries = QSpinBox()
        self.in_black_retries.setRange(1, 200)
        self.in_black_retries.setValue(50)
        self.in_black_delay = QSpinBox()
        self.in_black_delay.setRange(0, 120)
        self.in_black_delay.setValue(2)
        black.addWidget(QLabel("批量黑号检测"))
        black.addWidget(QLabel("429最大重试"))
        black.addWidget(self.in_black_retries)
        black.addWidget(QLabel("账号间隔(秒)"))
        black.addWidget(self.in_black_delay)
        for text in ("检测左侧筛选内已登录", "检测全部已登录", "按结果分组"):
            btn = QPushButton(text)
            btn.clicked.connect(lambda _, t=text: self.stub_log(f"[黑号检测] {t}：待接入"))
            black.addWidget(btn)
        black.addStretch(1)
        layout.addLayout(black)

        ops1 = QHBoxLayout()
        for text in ("一键全部HTTP长连接", "一键极速", "一键全部获取活动ID", "一键刷新地址", "合并地址备份"):
            btn = QPushButton(text)
            btn.clicked.connect(lambda _, t=text: self.stub_log(f"[全局操作] {t}：待接入"))
            ops1.addWidget(btn)
        ops1.addStretch(1)
        layout.addLayout(ops1)

        ops2 = QHBoxLayout()
        self.in_address_refresh_gap = QSpinBox()
        self.in_address_refresh_gap.setRange(0, 120000)
        self.in_address_refresh_gap.setSingleStep(100)
        self.in_address_refresh_gap.setValue(3000)
        ops2.addWidget(QLabel("地址刷新间隔(ms)"))
        ops2.addWidget(self.in_address_refresh_gap)
        for text in ("批量抢购", "停止抢购", "模拟节点中号链路", "批量组单", "批量下单", "批量订单地址修改", "批量设默认地址"):
            btn = QPushButton(text)
            btn.clicked.connect(lambda _, t=text: self.stub_log(f"[全局操作] {t}：待接入"))
            ops2.addWidget(btn)
        ops2.addStretch(1)
        layout.addLayout(ops2)

        parent_layout.addWidget(box)

    def _build_proxy_pool_panel(self, parent_layout: QVBoxLayout):
        box = QGroupBox("代理IP池管理（默认 SK5）")
        layout = QVBoxLayout(box)

        tools = QHBoxLayout()
        for text in ("刷新", "自检连通", "API提取", "查看分配", "清空", "保存"):
            btn = QPushButton(text)
            btn.clicked.connect(lambda _, t=text: self.stub_log(f"[代理池] {t}：待接入"))
            tools.addWidget(btn)
        tools.addStretch(1)
        layout.addLayout(tools)

        hint = QLabel("每行一条；四段 host/port/user/pass 会规范为 socks5://。完整 socks5://、socks5h://、http://、https:// 或纯 IP:端口均可。")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        api = QHBoxLayout()
        api_urls = QVBoxLayout()
        self.in_proxy_api1 = QLineEdit()
        self.in_proxy_api2 = QLineEdit()
        self.in_proxy_api3 = QLineEdit()
        self.in_proxy_api1.setPlaceholderText("代理API提取地址 1（GET）")
        self.in_proxy_api2.setPlaceholderText("代理API提取地址 2（可空）")
        self.in_proxy_api3.setPlaceholderText("代理API提取地址 3（可空）")
        api_urls.addWidget(self.in_proxy_api1)
        api_urls.addWidget(self.in_proxy_api2)
        api_urls.addWidget(self.in_proxy_api3)
        self.in_proxy_json_path = QLineEdit("data.list")
        self.in_proxy_json_path.setMaximumWidth(150)
        self.chk_proxy_http = QCheckBox("HTTP格式")
        self.chk_proxy_https = QCheckBox("HTTPS格式")
        api.addLayout(api_urls, 1)
        api.addWidget(QLabel("jsonPath"))
        api.addWidget(self.in_proxy_json_path)
        api.addWidget(self.chk_proxy_http)
        api.addWidget(self.chk_proxy_https)
        layout.addLayout(api)

        grid = QHBoxLayout()
        self.proxy_pool_editor = QPlainTextEdit()
        self.proxy_pool_editor.setPlaceholderText(
            "例如：\n122.246.5.148/19148/myuser/mypass  （四段→socks5）\n"
            "socks5://1.2.3.4:1080\nhttp://1.2.3.4:8080\nhttps://1.2.3.4:8443\n1.2.3.4:8080  （默认 socks5）"
        )
        self.proxy_pool_editor.setMaximumHeight(115)
        self.proxy_pool_list = QPlainTextEdit()
        self.proxy_pool_list.setReadOnly(True)
        self.proxy_pool_list.setPlainText("保存/API提取后显示规范化代理、账号均分与轮询分配预览。")
        self.proxy_pool_list.setMaximumHeight(115)
        grid.addWidget(self.proxy_pool_editor, 2)
        grid.addWidget(self.proxy_pool_list, 1)
        layout.addLayout(grid)
        parent_layout.addWidget(box)

    def _build_tab_rush(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # 账号和地址
        sel_row = QHBoxLayout()
        self.cmb_rush_phone = QComboBox()
        self.cmb_rush_addr = QComboBox()
        self.btn_refresh_addr = QPushButton("刷新地址")
        self.btn_refresh_addr.clicked.connect(self.load_addresses_for_rush)
        self.cmb_rush_phone.currentTextChanged.connect(self.on_rush_phone_changed)
        sel_row.addWidget(QLabel("操作账号"))
        sel_row.addWidget(self.cmb_rush_phone, 2)
        sel_row.addWidget(QLabel("收货地址"))
        sel_row.addWidget(self.cmb_rush_addr, 3)
        sel_row.addWidget(self.btn_refresh_addr)
        layout.addLayout(sel_row)

        # 抢购参数
        grid = QGridLayout()
        self.in_item = QLineEdit()
        self.in_item.setText("741")
        self.in_imtp = QLineEdit()
        self.in_imtp.setText("IMTP1000313")
        self.in_act = QLineEdit()
        self.in_amount = QSpinBox()
        self.in_amount.setRange(1, 99)
        self.in_amount.setValue(2)
        self.in_src = QLineEdit()
        self.in_retry = QSpinBox()
        self.in_retry.setRange(1, 200)
        self.in_retry.setValue(10)
        self.in_delay = QSpinBox()
        self.in_delay.setRange(0, 120000)
        self.in_delay.setValue(0)
        self.in_rounds = QSpinBox()
        self.in_rounds.setRange(1, 100)
        self.in_rounds.setValue(1)
        self.in_round_gap = QSpinBox()
        self.in_round_gap.setRange(0, 300000)
        self.in_round_gap.setSingleStep(50)
        self.in_round_gap.setValue(1000)
        self.in_round_jitter = QSpinBox()
        self.in_round_jitter.setRange(0, 60000)
        self.in_round_jitter.setValue(100)
        self.cmb_branch = QComboBox()
        self.cmb_branch.addItem("默认（普通抢购）", "")
        self.cmb_branch.addItem("分支一（如 741 飞天）", "one")
        self.cmb_branch.addItem("分支二", "two")
        self.cmb_branch.addItem("分支三", "three")
        self.cmb_branch.addItem("分支四", "four")
        self.cmb_branch.setCurrentIndex(1)
        self.in_referer = QLineEdit()
        self.in_referer.setText("https://h5.moutai519.com.cn/mt/item/smsp-detail?appConfig=2_1_2")

        grid.addWidget(QLabel("商品 ID（code）"), 0, 0)
        grid.addWidget(self.in_item, 0, 1)
        grid.addWidget(QLabel("编号（spuId）"), 0, 2)
        grid.addWidget(self.in_imtp, 0, 3)
        grid.addWidget(QLabel("活动 ID（可空，按编号自动拉取）"), 0, 4)
        grid.addWidget(self.in_act, 0, 5)
        grid.addWidget(QLabel("数量"), 0, 6)
        grid.addWidget(self.in_amount, 0, 7)

        grid.addWidget(QLabel("来源 ID（可选）"), 1, 0)
        grid.addWidget(self.in_src, 1, 1)
        grid.addWidget(QLabel("重试次数"), 1, 2)
        grid.addWidget(self.in_retry, 1, 3)
        grid.addWidget(QLabel("间隔(ms)"), 1, 4)
        grid.addWidget(self.in_delay, 1, 5)
        grid.addWidget(QLabel("轮次"), 1, 6)
        grid.addWidget(self.in_rounds, 1, 7)

        grid.addWidget(QLabel("轮次间隔(ms)"), 2, 0)
        grid.addWidget(self.in_round_gap, 2, 1)
        grid.addWidget(QLabel("轮次抖动±ms"), 2, 2)
        grid.addWidget(self.in_round_jitter, 2, 3)
        grid.addWidget(QLabel("抢购 API 分支（自动）"), 2, 4)
        grid.addWidget(self.cmb_branch, 2, 5)
        grid.addWidget(QLabel("H5 商品详情页（referer）"), 3, 0)
        grid.addWidget(self.in_referer, 3, 1, 1, 7)
        layout.addLayout(grid)

        mode_row = QHBoxLayout()
        self.chk_fast_loop = QCheckBox("极速（每枪一次 API，与普通一致）")
        self.chk_mt_mode = QCheckBox("MT模式")
        self.chk_mt_no_wait = QCheckBox("MT不等响应")
        self.chk_legacy_bb = QCheckBox("老 BB 算法（SM4）")
        self.chk_need_code = QCheckBox("抢购体带 code 字段（自动）")
        for w in (self.chk_fast_loop, self.chk_mt_mode, self.chk_mt_no_wait, self.chk_legacy_bb, self.chk_need_code):
            mode_row.addWidget(w)
        mode_row.addStretch(1)
        layout.addLayout(mode_row)

        btn_row = QHBoxLayout()
        self.btn_rush = QPushButton("开始抢购")
        self.btn_fill_item = QPushButton("填入已选商品")
        self.btn_resolve_route = QPushButton("自动匹配编号/分支")
        self.btn_rush.clicked.connect(self.start_rush)
        self.btn_fill_item.clicked.connect(self.fill_from_selected)
        self.btn_resolve_route.clicked.connect(lambda: self.stub_log("[抢购] 自动匹配编号/分支：待接入"))
        btn_row.addWidget(self.btn_rush)
        btn_row.addWidget(self.btn_fill_item)
        btn_row.addWidget(self.btn_resolve_route)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        node_hint = QLabel("Node 桥说明：每枪明细写入账号日志；默认不再在 Node 网页/终端刷屏。固定入口：主 WebUI 口 + /node_rush_panel。")
        node_hint.setWordWrap(True)
        layout.addWidget(node_hint)

        self.rush_result = QTextEdit()
        self.rush_result.setReadOnly(True)
        self.rush_result.setMaximumHeight(120)
        layout.addWidget(self.rush_result)

        # 组单/下单/支付
        form = QFormLayout()
        self.in_record_id = QLineEdit()
        self.in_compose_cnt = QSpinBox()
        self.in_compose_cnt.setRange(1, 99)
        self.in_compose_cnt.setValue(1)
        form.addRow("优先购记录号", self.in_record_id)
        form.addRow("组单/下单件数", self.in_compose_cnt)
        layout.addLayout(form)

        order_row = QHBoxLayout()
        self.cmb_pay_channel = QComboBox()
        self.cmb_pay_channel.addItem("支付宝 (70)", 70)
        self.cmb_pay_channel.addItem("微信支付 (60)", 60)
        self.cmb_pay_channel.addItem("云闪付 (80)", 80)
        self.btn_compose = QPushButton("组单")
        self.btn_submit = QPushButton("下单")
        self.btn_pay = QPushButton("支付")
        self.btn_compose.clicked.connect(self.do_compose)
        self.btn_submit.clicked.connect(self.do_submit)
        self.btn_pay.clicked.connect(self.do_pay)
        order_row.addWidget(self.cmb_pay_channel)
        order_row.addWidget(self.btn_compose)
        order_row.addWidget(self.btn_submit)
        order_row.addWidget(self.btn_pay)
        order_row.addStretch(1)
        layout.addLayout(order_row)

        self.order_result = QTextEdit()
        self.order_result.setReadOnly(True)
        self.order_result.setMaximumHeight(120)
        layout.addWidget(self.order_result)

        self.tabs.addTab(tab, "抢购操作")

    def _build_tab_items(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        top = QHBoxLayout()
        self.item_search = QLineEdit()
        self.item_search.setPlaceholderText("名称 / 编码 / actId…")
        self.item_search.textChanged.connect(self.filter_items)
        self.btn_items_refresh = QPushButton("刷新")
        self.btn_items_refresh.clicked.connect(self.load_items)
        top.addWidget(self.item_search, 1)
        top.addWidget(self.btn_items_refresh)
        layout.addLayout(top)

        self.item_list = QListWidget()
        self.item_list.itemSelectionChanged.connect(self.on_select_item)
        layout.addWidget(self.item_list, 1)

        self.tabs.addTab(tab, "商品管理")

    def _build_tab_log(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        row = QHBoxLayout()
        row.addWidget(QLabel("操作日志"), 1)
        btn_clear = QPushButton("清空")
        btn_clear.clicked.connect(lambda: self.log_box.clear())
        row.addWidget(btn_clear)
        layout.addLayout(row)

        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.log_box.setStyleSheet(
            "QPlainTextEdit { background:#151515; color:#f2c94c; "
            "font-family:Consolas, 'Microsoft YaHei UI'; font-size:12px; }"
        )
        layout.addWidget(self.log_box, 1)
        self.tabs.addTab(tab, "操作日志")

    def _build_tab_pay(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("支付通知（上：订单信息；下：支付链接竖向展开）"), 1)
        self.cmb_pay_channel_log = QComboBox()
        self.cmb_pay_channel_log.addItem("支付宝 (70)", 70)
        self.cmb_pay_channel_log.addItem("微信支付 (60)", 60)
        self.cmb_pay_channel_log.addItem("云闪付 (80)", 80)
        btn_export = QPushButton("导出")
        btn_clear = QPushButton("清空通知")
        btn_export.clicked.connect(lambda: self.stub_log("[支付通知] 导出：待接入"))
        btn_clear.clicked.connect(lambda: self.pay_notice_box.clear())
        toolbar.addWidget(self.cmb_pay_channel_log)
        toolbar.addWidget(btn_export)
        toolbar.addWidget(btn_clear)
        layout.addLayout(toolbar)

        hint = QLabel(
            "自动链路或手动拉支付成功后写入本表；复制含手机号/订单号/链接；是否成功走订单接口。"
            "15分倒计时自本行时间起算。"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.pay_notice_box = QPlainTextEdit()
        self.pay_notice_box.setReadOnly(True)
        self.pay_notice_box.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.pay_notice_box.setStyleSheet(
            "QPlainTextEdit { background:#ffffff; color:#1f2937; "
            "font-family:Consolas, 'Microsoft YaHei UI'; font-size:12px; }"
        )
        self.pay_notice_box.setPlainText(
            "时间\t账号\t商品\t规格\t瓶数\t金额(约)\t15分倒计时\n"
            "支付链接会在订单行下方竖向展开；当前桌面壳先保留展示区，自动写入逻辑待接入。"
        )
        layout.addWidget(self.pay_notice_box, 1)
        self.tabs.addTab(tab, "支付通知")

    def _build_tab_shield(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        top = QHBoxLayout()
        btn_self_check = QPushButton("引擎自检")
        btn_build = QPushButton("生成防护头")
        btn_self_check.clicked.connect(self.shield_self_check)
        btn_build.clicked.connect(self.shield_build_headers_demo)
        top.addWidget(btn_self_check)
        top.addWidget(btn_build)
        top.addStretch(1)
        layout.addLayout(top)

        self.shield_output = QTextEdit()
        self.shield_output.setReadOnly(True)
        layout.addWidget(self.shield_output, 1)
        self.tabs.addTab(tab, "Shield引擎")

    # -------------------- 辅助 --------------------
    def log(self, msg: str):
        ts = time.strftime("%H:%M:%S")
        if hasattr(self, "log_box"):
            self.log_box.appendPlainText(f"[{ts}] {msg}")
            self.log_box.verticalScrollBar().setValue(self.log_box.verticalScrollBar().maximum())

    def stub_log(self, msg: str):
        self.log(msg)

    def show_distributed_nodes(self):
        self.show_msg(
            "分布式节点",
            "当前桌面壳已保留节点入口。\n"
            "在线节点、CPU、任务、命中、TLS/代理失败统计需要接入 distributed_worker.py 状态接口后显示。",
        )

    def show_msg(self, title: str, text: str, error: bool = False):
        if error:
            QMessageBox.critical(self, title, text)
        else:
            QMessageBox.information(self, title, text)

    def _selected_phone(self) -> str | None:
        item = self.acct_list.currentItem()
        if not item:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _entry_by_phone(self, phone: str):
        return POOL.get(phone)

    def _addr_text(self, addr: dict) -> str:
        return f"{addr.get('name','')} {addr.get('mobile','')} - {addr.get('fullAddress') or addr.get('address') or ''}"

    # -------------------- 账号相关 --------------------
    def load_accounts(self):
        self._accounts_cache = POOL.snapshot()
        self.render_accounts()
        st = POOL.stats()
        self.lbl_stats.setText(
            f"账号总数 {st.get('total',0)} | 已登录 {st.get('logged_in',0)} | 已发码 {st.get('code_sent',0)} | 失败 {st.get('failed',0)}"
        )
        self.update_rush_phone_select()

    def render_accounts(self):
        q = self.search_input.text().strip().lower()
        self.acct_list.clear()
        for a in self._accounts_cache:
            if q and q not in a.get("phone", "") and q not in a.get("nickname", "").lower():
                continue
            title = f"{a.get('nickname') or a.get('phone')} ({a.get('phone')}) [{a.get('status')}]"
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, a.get("phone"))
            self.acct_list.addItem(item)
        if hasattr(self, "group_buttons"):
            total = len(self._accounts_cache)
            logged = sum(1 for a in self._accounts_cache if a.get("status") == STATUS_OK)
            black = sum(1 for a in self._accounts_cache if "黑" in str(a.get("risk_tag") or a.get("remark") or ""))
            white = max(0, logged - black)
            success = sum(1 for a in self._accounts_cache if a.get("rush_success_record") or a.get("priorityRecordId"))
            counts = [total, white, black, success]
            names = ["全部", "白号", "黑号", "抢购成功"]
            for btn, name, count in zip(self.group_buttons, names, counts):
                btn.setText(f"{name} {count}")

    def on_select_account(self):
        phone = self._selected_phone()
        if not phone:
            return
        self.selected_phone = phone
        e = self._entry_by_phone(phone)
        if not e:
            return
        detail = {
            "phone": e.phone,
            "nickname": e.nickname,
            "status": e.status,
            "uid": e.uid,
            "token_head": (e.token[:24] + "...") if e.token else "",
            "mfr": e.mfr,
            "mdl": e.mdl,
            "scr": e.scr,
        }
        self.detail_info.setPlainText(json.dumps(detail, ensure_ascii=False, indent=2))

    def add_account(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("添加账号")
        lay = QFormLayout(dlg)
        in_phone = QLineEdit()
        in_nick = QLineEdit()
        lay.addRow("手机号", in_phone)
        lay.addRow("昵称(可选)", in_nick)
        btn_ok = QPushButton("添加并发送验证码")
        btn_ok.clicked.connect(dlg.accept)
        lay.addRow(btn_ok)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        phone = in_phone.text().strip()
        nick = in_nick.text().strip()
        if not phone:
            self.show_msg("错误", "请输入手机号", True)
            return
        POOL.add(phone, nick)
        res = POOL.send_code(phone)
        self.log(f"[添加账号] {phone}")
        self.log(f"[发码] {phone} -> {res}")
        self.load_accounts()

    def batch_import(self):
        text, ok = QInputDialog.getMultiLineText(self, "批量导入", "手机号列表（每行一个）")
        if not ok:
            return
        phones = [x.strip() for x in text.replace(",", "\n").replace("，", "\n").splitlines() if x.strip()]
        if not phones:
            self.show_msg("错误", "请输入手机号", True)
            return
        result = POOL.add_batch(phones)
        self.log(f"[批量导入] 输入 {len(phones)} 个，处理 {len(result)} 个")
        self.load_accounts()

    def batch_send_code(self):
        pending = [a["phone"] for a in POOL.snapshot() if a.get("status") != STATUS_OK]
        if not pending:
            self.show_msg("提示", "没有需要发码的账号")
            return
        if QMessageBox.question(self, "确认", f"将对 {len(pending)} 个账号发送验证码，继续？") != QMessageBox.StandardButton.Yes:
            return
        results = POOL.batch_send_code(pending, delay=0.5)
        ok = sum(1 for r in results if (r.get("result") or {}).get("code") == 2000)
        self.log(f"[批量发码] 成功 {ok}/{len(results)}")
        self.load_accounts()

    def batch_refresh_login_all(self):
        logged = [e.phone for e in POOL.all() if e.status == STATUS_OK]
        if not logged:
            self.show_msg("提示", "无已登录账号")
            return
        if (
            QMessageBox.question(
                self,
                "确认",
                f"将对 {len(logged)} 个账号校验收货地址（Session 与 JWT 设备对齐，不换机模，避免 4011）。\n"
                f"默认每号间隔约 4s。继续？",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.log(f"[全部刷新登录] → {len(logged)} 个账号（JWT 对齐 + ship/query）…")
        results = POOL.batch_refresh_login(delay=4.0)
        okc = sum(1 for r in results if r.get("ok"))
        self.log(f"[全部刷新登录] 完成 {okc}/{len(results)}")
        for r in results:
            if not r.get("ok"):
                self.log(f"  [{r.get('phone')}] {r.get('message', '失败')}")
        self.load_accounts()

    def send_code_for_selected(self):
        phone = self.selected_phone
        if not phone:
            self.show_msg("提示", "请先选择账号")
            return
        res = POOL.send_code(phone)
        self.log(f"[发码] {phone} -> {res}")
        self.load_accounts()

    def login_selected(self):
        phone = self.selected_phone
        if not phone:
            self.show_msg("提示", "请先选择账号")
            return
        code, ok = QInputDialog.getText(self, "验证码登录", f"账号 {phone} 验证码:")
        if not ok or not code.strip():
            return
        res = POOL.login(phone, code.strip())
        self.log(f"[登录] {phone} -> {res}")
        self.load_accounts()
        self.on_select_account()

    def load_addr_for_selected(self):
        phone = self.selected_phone
        if not phone:
            self.show_msg("提示", "请先选择账号")
            return
        e = self._entry_by_phone(phone)
        if not e:
            return
        if e.status != STATUS_OK:
            self.show_msg("提示", "该账号尚未登录", True)
            return
        s = e.ensure_session()
        addrs = s.addresses()
        self.current_addresses = addrs
        if addrs:
            self.selected_addr = addrs[0]
        lines = [f"[{i}] {self._addr_text(a)}" for i, a in enumerate(addrs)]
        self.addr_box.setPlainText("\n".join(lines) if lines else "无收货地址")
        self.log(f"[地址] {phone} -> {len(addrs)} 个")
        self.on_rush_phone_changed(self.cmb_rush_phone.currentText())

    def remove_selected(self):
        phone = self.selected_phone
        if not phone:
            return
        if QMessageBox.question(self, "确认删除", f"确认删除账号 {phone}？") != QMessageBox.StandardButton.Yes:
            return
        from grab_bot_ops import grab_remove_account

        grab_remove_account(POOL, phone)
        self.selected_phone = None
        self.detail_info.clear()
        self.addr_box.clear()
        self.log(f"[删除账号] {phone}")
        self.load_accounts()

    # -------------------- 抢购 --------------------
    def update_rush_phone_select(self):
        cur = self.cmb_rush_phone.currentData()
        self.cmb_rush_phone.clear()
        self.cmb_rush_phone.addItem("— 请选择已登录账号 —", "")
        for a in POOL.snapshot():
            if a.get("status") == STATUS_OK:
                label = f"{a.get('nickname') or a.get('phone')} ({a.get('phone')})"
                self.cmb_rush_phone.addItem(label, a.get("phone"))
        idx = self.cmb_rush_phone.findData(cur)
        if idx >= 0:
            self.cmb_rush_phone.setCurrentIndex(idx)

    def on_rush_phone_changed(self, _):
        self.load_addresses_for_rush()

    def load_addresses_for_rush(self):
        phone = self.cmb_rush_phone.currentData()
        self.cmb_rush_addr.clear()
        if not phone:
            self.cmb_rush_addr.addItem("— 先选账号 —")
            return
        e = self._entry_by_phone(phone)
        if not e or e.status != STATUS_OK:
            self.cmb_rush_addr.addItem("— 账号未登录 —")
            return
        addrs = e.ensure_session().addresses()
        self.current_addresses = addrs
        if not addrs:
            self.cmb_rush_addr.addItem("— 无地址 —")
            self.selected_addr = None
            return
        for i, addr in enumerate(addrs):
            self.cmb_rush_addr.addItem(self._addr_text(addr), i)
        self.selected_addr = addrs[0]

    def fill_from_selected(self):
        if not self.selected_item:
            self.show_msg("提示", "请先在商品管理里选择商品", True)
            return
        self.in_item.setText(self.selected_item["code"])
        self.in_imtp.setText(str(self.selected_item.get("spuId") or "").strip())
        self.in_act.setText(str(self.selected_item.get("actId") or "").strip())
        ref = self.selected_item.get("referer") or self.selected_item.get("ref") or self.selected_item.get("url")
        if ref:
            self.in_referer.setText(str(ref))
        branch = self.selected_item.get("branch")
        if branch is not None:
            idx = self.cmb_branch.findData(str(branch))
            if idx >= 0:
                self.cmb_branch.setCurrentIndex(idx)
        if self.selected_item.get("needcode") is not None:
            self.chk_need_code.setChecked(bool(self.selected_item.get("needcode")))
        self.tabs.setCurrentIndex(1)

    def start_rush(self):
        phone = self.cmb_rush_phone.currentData()
        item = self.in_item.text().strip()
        imtp = self.in_imtp.text().strip()
        act = self.in_act.text().strip()
        amount = str(self.in_amount.value())
        src = self.in_src.text().strip()
        retry = self.in_retry.value()
        delay_ms = self.in_delay.value()
        rounds = self.in_rounds.value()
        round_gap = self.in_round_gap.value()
        round_jitter = self.in_round_jitter.value()
        branch = self.cmb_branch.currentData() or ""
        mode_bits = []
        if self.chk_fast_loop.isChecked():
            mode_bits.append("极速")
        if self.chk_mt_mode.isChecked():
            mode_bits.append("MT模式")
        if self.chk_mt_no_wait.isChecked():
            mode_bits.append("MT不等响应")
        if self.chk_legacy_bb.isChecked():
            mode_bits.append("老BB")
        if self.chk_need_code.isChecked():
            mode_bits.append("带code")

        if not phone:
            self.show_msg("错误", "请选择账号", True)
            return
        if not item:
            self.show_msg("错误", "请填写商品 ID（code）", True)
            return
        if not act:
            self.show_msg("错误", "请填写活动 ID（itemPriorityActId）；带「act」标签的商品可从列表自动填入", True)
            return
        e = self._entry_by_phone(phone)
        if not e or e.status != STATUS_OK:
            self.show_msg("错误", "账号未登录", True)
            return
        s = e.ensure_session()

        self.btn_rush.setEnabled(False)
        self.rush_result.clear()
        self.log(
            f"[抢购] {phone} 商品ID={item} 编号={imtp or '-'} 活动={act or '自动'} "
            f"分支={branch or '默认'} 重试={retry} 间隔={delay_ms}ms 轮次={rounds} "
            f"轮次间隔={round_gap}ms 抖动±{round_jitter}ms"
            + (f" 模式={'/'.join(mode_bits)}" if mode_bits else "")
        )
        QApplication.processEvents()
        last_res = None

        for i in range(1, retry + 1):
            self.rush_result.setPlainText(f"第 {i}/{retry} 次尝试...")
            QApplication.processEvents()
            res = s.rush(item=item, act_id=act, amount=amount, src=src)
            last_res = res
            if res.get("code") == 2000:
                rid = (res.get("data") or {}).get("priorityRecordId", "")
                self.in_record_id.setText(str(rid))
                self.in_compose_cnt.setValue(int(amount))
                self.rush_result.setPlainText(
                    "抢购成功\n" + json.dumps(res, ensure_ascii=False, indent=2)
                )
                self.log(f"[抢购] 成功 phone={phone} item={item} act={act} rid={rid}")
                try:
                    if POOL.pin_h5_device_after_rush_ok(phone, session=s, rush_response=res):
                        self.log(
                            "[抢购] 已写死设备；中号出口已落盘，组单将固定该代理线（见 accounts rush_success_*）"
                        )
                    else:
                        self.log("[抢购] 警告：写死快照失败，请用 Web 抢购或检查账号会话")
                except Exception as ex:
                    self.log(f"[抢购] 写死快照异常: {ex}")
                break
            self.log(f"[抢购] 第{i}次失败: {res}")
            if i < retry:
                time.sleep(delay_ms / 1000.0)
                QApplication.processEvents()

        if last_res and last_res.get("code") != 2000:
            self.rush_result.setPlainText("抢购未成功\n" + json.dumps(last_res, ensure_ascii=False, indent=2))

        self.btn_rush.setEnabled(True)

    def _current_addr_from_combo(self) -> dict | None:
        idx = self.cmb_rush_addr.currentData()
        if idx is None or not isinstance(idx, int):
            return None
        if idx < 0 or idx >= len(self.current_addresses):
            return None
        return self.current_addresses[idx]

    def do_compose(self):
        phone = self.cmb_rush_phone.currentData()
        rec_id = self.in_record_id.text().strip()
        spu = self.in_item.text().strip()
        cnt = self.in_compose_cnt.value()
        if not phone or not rec_id or not spu:
            self.show_msg("错误", "请确保账号/recordId/商品编码已填写", True)
            return
        e = self._entry_by_phone(phone)
        if not e or e.status != STATUS_OK:
            self.show_msg("错误", "账号未登录", True)
            return
        addr = self._current_addr_from_combo()
        if not addr:
            self.show_msg("错误", "请先选择地址", True)
            return
        rot = 1
        try:
            from proxy_pool import active_urls, pool_is_configured

            if pool_is_configured() and active_urls():
                n = len(active_urls())
                try:
                    cap = int(os.environ.get("MT_COMPOSE_POOL_ROTATE_MAX", str(n)) or n)
                except (TypeError, ValueError):
                    cap = n
                rot = max(1, min(cap, 500))
        except Exception:
            rot = 1
        res = e.ensure_session().compose(
            spu=spu, cnt=cnt, rec_id=rec_id, addr=addr, rotate_max=rot
        )
        self.order_result.setPlainText(json.dumps(res, ensure_ascii=False, indent=2))
        self.log(f"[组单] {res}")

    def do_submit(self):
        phone = self.cmb_rush_phone.currentData()
        rec_id = self.in_record_id.text().strip()
        spu = self.in_item.text().strip()
        cnt = self.in_compose_cnt.value()
        if not phone or not rec_id or not spu:
            self.show_msg("错误", "请确保账号/recordId/商品编码已填写", True)
            return
        e = self._entry_by_phone(phone)
        if not e or e.status != STATUS_OK:
            self.show_msg("错误", "账号未登录", True)
            return
        addr = self._current_addr_from_combo()
        if not addr:
            self.show_msg("错误", "请先选择地址", True)
            return
        res = e.ensure_session().submit(spu=spu, cnt=cnt, rec_id=rec_id, addr=addr)
        self.order_result.setPlainText(json.dumps(res, ensure_ascii=False, indent=2))
        self.log(f"[下单] {res}")

    def do_pay(self):
        phone = self.cmb_rush_phone.currentData()
        if not phone:
            self.show_msg("错误", "请先选择账号", True)
            return
        oid, ok = QInputDialog.getText(self, "支付", "订单ID (orderId):")
        if not ok or not oid.strip():
            return
        e = self._entry_by_phone(phone)
        if not e or e.status != STATUS_OK:
            self.show_msg("错误", "账号未登录", True)
            return
        channel = self.cmb_pay_channel.currentData() or 70
        res = e.ensure_session().pay(oid.strip(), method=0, channel=str(channel))
        self.order_result.setPlainText(json.dumps(res, ensure_ascii=False, indent=2))
        self.log(f"[支付] channel={channel} {res}")

    # -------------------- 商品 --------------------
    def load_items(self):
        # 桌面端默认带全部分类，便于选其它 SKU；与 Web 默认仅 ITEM_LIST 不同
        self.all_items = load_catalog_items(include_full_product_list=True)
        if not self.all_items:
            self.item_list.clear()
            self.item_list.addItem("未找到商品：请放置 product_list.json（与 mt 目录同步）")
            return
        self.filter_items()

    def filter_items(self):
        q = self.item_search.text().strip().lower()
        self.item_list.clear()
        data = self.all_items
        if q:
            data = [
                i for i in data
                if q in i["name"].lower()
                or q in i["code"].lower()
                or q in (i.get("spuId") or i.get("actId") or "").lower()
            ]
        if not data:
            self.item_list.addItem("没有匹配商品")
            return
        for i in data[:200]:
            spu = i.get("spuId") or i.get("actId") or ""
            act = i.get("actId")
            tail = f"{spu}" + (f" | act {act}" if act else "")
            title = f"{i['name']} | ¥{i['price']} | {i['code']} | {tail}"
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, i)
            self.item_list.addItem(item)

    def on_select_item(self):
        item = self.item_list.currentItem()
        if not item:
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(data, dict):
            return
        self.selected_item = data
        spu = data.get("spuId") or data.get("actId") or ""
        self.log(f"[选商品] {data['name']} ({data['code']}, {spu})")

    # -------------------- shield --------------------
    def shield_self_check(self):
        try:
            rid = make_raw_id(aid="desktop_ui_test")
            dev_token = make_device_token(rid)
            h5id = make_h5_devid()
            h5sid = make_h5_session()
            k, v = make_kv_pair(rid)
            enc = seal_param({"test": True, "ts": int(time.time() * 1000)})
            dec = unseal_param(enc)
            out = {
                "raw_id": rid,
                "device_token_prefix": dev_token[:28] + "...",
                "h5_devid": h5id,
                "h5_sessid": h5sid,
                "mt_k": k,
                "mt_v": v,
                "param_encrypt_ok": dec.get("test") is True,
            }
            self.shield_output.setPlainText(json.dumps(out, ensure_ascii=False, indent=2))
            self.log("[shield] 引擎自检完成")
        except Exception as e:
            self.shield_output.setPlainText(f"自检失败: {e}")
            self.log(f"[shield] 自检失败: {e}")

    def shield_build_headers_demo(self):
        try:
            did = make_h5_devid()
            sid = make_h5_session()
            body = {"actParam": seal_param({"demo": "rush", "amount": "1"})}
            bb = shield_for_post(body, devid=did, sessid=sid, uid="0", rush=False)
            dec = decrypt_shield(bb["Content-Web-Bb"])
            out = {
                "devid": did,
                "sessid": sid,
                "headers": {
                    "Content-Web-Bb_prefix": bb["Content-Web-Bb"][:50] + "...",
                    "Sdk-Ver-Bb": bb["Sdk-Ver-Bb"],
                    "Content-Hh-Bb": bb["Content-Hh-Bb"],
                },
                "decoded_fields_preview": {
                    "appid": dec["fields"].get("appid"),
                    "devid": dec["fields"].get("devid"),
                    "sessid": dec["fields"].get("sessid"),
                    "uid": dec["fields"].get("uid"),
                },
            }
            self.shield_output.setPlainText(json.dumps(out, ensure_ascii=False, indent=2))
            self.log("[shield] 防护头生成成功")
        except Exception as e:
            self.shield_output.setPlainText(f"防护头生成失败: {e}")
            self.log(f"[shield] 防护头生成失败: {e}")


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
