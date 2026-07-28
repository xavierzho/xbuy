#!/usr/bin/env python3
from __future__ import annotations

import sys
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QPushButton, QScrollArea, QTabWidget, QVBoxLayout, QWidget


class Win(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.resize(1620, 980)
        self.setWindowTitle("i茅台助手 - 1:1 复刻壳")
        root = QWidget()
        self.setCentralWidget(root)
        out = QVBoxLayout(root); out.setContentsMargins(0, 0, 0, 0); out.setSpacing(0)

        top = QFrame(); top.setObjectName("top")
        th = QHBoxLayout(top); th.setContentsMargins(10, 8, 10, 8); th.setSpacing(8)
        th.addWidget(QLabel("∑")); th.addWidget(QLabel("i茅台科技")); th.addWidget(QLabel("内测定制 v2.0")); th.addStretch(1)
        th.addWidget(QPushButton("最小化")); th.addWidget(QPushButton("关闭"))
        out.addWidget(top)

        body = QHBoxLayout(); body.setContentsMargins(0, 0, 0, 0); body.setSpacing(0)
        body.addWidget(self.left_panel(), 0)
        body.addWidget(self.right_panel(), 1)
        out.addLayout(body, 1)

    def left_panel(self) -> QWidget:
        p = QFrame(); p.setObjectName("left"); p.setFixedWidth(330)
        v = QVBoxLayout(p); v.setContentsMargins(10, 10, 10, 10); v.setSpacing(8)
        v.addWidget(QLabel("账号管理"))
        s = QLineEdit(); s.setPlaceholderText("搜索手机号/昵称..."); v.addWidget(s)

        r1 = QHBoxLayout()
        for t in ("刷新列表", "+ 新增", "批量导入", "全选"): r1.addWidget(QPushButton(t))
        v.addLayout(r1)
        r2 = QHBoxLayout()
        for t in ("清空勾选", "补全账号当天", "导出勾选手机号"): r2.addWidget(QPushButton(t))
        v.addLayout(r2)
        r3 = QHBoxLayout()
        for t in ("删除勾选", "清除全部停抢", "批量发码"): r3.addWidget(QPushButton(t))
        v.addLayout(r3)

        stat = QFrame(); stat.setObjectName("soft")
        sl = QHBoxLayout(stat); sl.setContentsMargins(8, 6, 8, 6)
        sl.addWidget(QLabel("总数 109")); sl.addWidget(QLabel("登录 26")); sl.addWidget(QLabel("死号 33")); sl.addStretch(1)
        v.addWidget(stat)
        r4 = QHBoxLayout()
        for t in ("+ 新建分组", "移入分组", "移出分组"): r4.addWidget(QPushButton(t))
        v.addLayout(r4)

        lst = QListWidget(); lst.setObjectName("list")
        for i in range(18): lst.addItem(QListWidgetItem(f"{i+1}. 17347334{i:02d}    已登录"))
        v.addWidget(lst, 1)
        return p

    def right_panel(self) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(10, 10, 10, 10); v.setSpacing(8)
        tabs = QTabWidget()
        tabs.addTab(self.mk_card_tab("抢购配置区域（占位）"), "抢购配置")
        tabs.addTab(self.ops_tab(), "抢购操作")
        tabs.addTab(self.mk_card_tab("商品管理区域（占位）"), "商品管理")
        tabs.addTab(self.mk_card_tab("操作日志区域（占位）"), "操作日志")
        tabs.addTab(self.mk_card_tab("支付通知区域（占位）"), "支付通知")
        tabs.setCurrentIndex(1)
        v.addWidget(tabs)
        return w

    def mk_card_tab(self, text: str) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w)
        c = QFrame(); c.setObjectName("card")
        cl = QVBoxLayout(c); cl.addWidget(QLabel(text))
        l.addWidget(c); l.addStretch(1); return w

    def ops_tab(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w)
        top = QFrame(); top.setObjectName("soft")
        th = QHBoxLayout(top); th.setContentsMargins(10, 6, 10, 6)
        th.addWidget(QLabel("北京时间 2026/4/29 01:52:17")); th.addStretch(1)
        for t in ("总 109", "登录 107", "死号 0", "失败 2"): th.addWidget(QLabel(t))
        th.addWidget(QPushButton("一键抢购"))
        l.addWidget(top)

        sc = QScrollArea(); sc.setWidgetResizable(True)
        inner = QWidget(); il = QVBoxLayout(inner); il.setSpacing(10)
        for i in range(8):
            il.addWidget(self.detail_card(i))
        il.addStretch(1)
        sc.setWidget(inner); l.addWidget(sc, 1)
        return w

    def detail_card(self, i: int) -> QWidget:
        c = QFrame(); c.setObjectName("card")
        cl = QVBoxLayout(c); cl.setContentsMargins(12, 10, 12, 10); cl.setSpacing(8)
        r1 = QHBoxLayout(); r1.addWidget(QLabel(f"未备注 135074833{i}")); r1.addWidget(QLabel(f"UID 11749114{i:02d}")); r1.addStretch(1); r1.addWidget(QLabel("4-26随治")); r1.addWidget(QPushButton("保存")); cl.addLayout(r1)
        r2 = QHBoxLayout(); r2.addWidget(QLabel("收货地址")); r2.addWidget(QLineEdit("湖南省长沙市岳麓区含浦街道工业示范园A1栋404号"), 1)
        for t in ("刷新地址", "新增地址", "查询实名", "提交实名"): r2.addWidget(QPushButton(t))
        cl.addLayout(r2)
        r3 = QHBoxLayout()
        for t in ("开始抢购", "高险抢抢", "HTTP长连接", "组单", "下单", "订单地址修改", "拉支付链接", "查订单", "确认收货"): r3.addWidget(QPushButton(t))
        r3.addStretch(1); r3.addWidget(QPushButton("删除账号")); r3.addWidget(QPushButton("注销账号")); cl.addLayout(r3)
        cl.addWidget(QLabel("• 常规抢购参数（商品 ID、编号、H5 详情页、分支与 code 自动对照）"))
        cl.addWidget(QLabel("[00:45:44] 一键配置『飞天53xx』500ml 分支=one"))
        return c


def main() -> None:
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei UI", 10))
    app.setStyleSheet(
        "QWidget{background:#f3f5f7;color:#1f2937;font-size:13px;}#top{background:#3f5562;}#top QLabel{color:#f1f5f9;}#left{background:#f7f8fa;border-right:1px solid #e4e9ef;}#soft{background:#eef2f6;border:1px solid #d7dee7;border-radius:9px;}#list{background:#fff;border:1px solid #dce4ec;border-radius:9px;}#card{background:#fff;border:1px solid #dfe6ee;border-radius:11px;}QPushButton{background:#fff;border:1px solid #d0d7df;border-radius:7px;padding:5px 10px;}QLineEdit,QComboBox{background:#fff;border:1px solid #d3dae3;border-radius:8px;padding:7px 9px;}QTabBar::tab{background:#edf1f5;border:1px solid #d8dee6;border-radius:8px;padding:7px 14px;margin-right:6px;}QTabBar::tab:selected{background:#fff;border-color:#b8944e;color:#6f5319;font-weight:600;}"
    )
    w = Win()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

