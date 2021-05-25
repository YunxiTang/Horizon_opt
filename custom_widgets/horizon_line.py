from PyQt5.QtWidgets import QWidget, QGridLayout, QRubberBand, QPushButton, QTabWidget, QHBoxLayout, QVBoxLayout, QCheckBox, QSpacerItem, QSizePolicy
from PyQt5.QtCore import Qt, QRect, QSize, QPoint, pyqtSignal, pyqtSlot, QMimeData, QDataStream, QIODevice, QModelIndex
from PyQt5.QtGui import QStandardItemModel

from custom_widgets import spinbox_line

import os
from custom_widgets.multi_slider import QMultiSlider

#
# class TabButtonWidget(QWidget):
#     def __init__(self, parent):
#         super().__init__(parent)
#         # Create button's
#         self.button_add = QPushButton("+")
#         self.button_remove = QPushButton("-")
#
#         # Set button size
#         self.button_add.setFixedSize(16, 16)
#         self.button_remove.setFixedSize(16, 16)
#
#         # Create layout
#         self.layout = QVBoxLayout()
#         self.layout.setSpacing(0)
#         self.layout.setContentsMargins(0, 0, 0, 0)
#
#         # Add button's to layout
#         self.layout.addWidget(self.button_add)
#         self.layout.addWidget(self.button_remove)
#
#         # Use layout in widget
#         self.setLayout(self.layout)

class FunctionTab(QWidget):
    def __init__(self, name, fun, n_nodes, parent=None):
        super().__init__(parent)

        self.name = name
        self.fun = fun
        self.n_nodes = n_nodes

        self.hlayout = QHBoxLayout(self)
        self.slider = QMultiSlider(slider_range=[0, self.n_nodes - 1, 1], values=[2, 3])
        self.hlayout.addWidget(self.slider)

        # adding + - push button to tab
        # self.ct_tab.tabBar().setTabButton(0, self.ct_tab.tabBar().RightSide, TabButtonWidget())

class HorizonLine(QWidget):
    add_fun_horizon = pyqtSignal(dict)
    remove_fun_horizon = pyqtSignal(dict)
    repeated_fun = pyqtSignal(str)

    def __init__(self, horizon, fun_type, logger=None, parent=None):
        QWidget.__init__(self, parent)
        self.setAcceptDrops(True)

        self.horizon_receiver = horizon

        if logger:
            self.logger = logger
        # can be Constraint or Cost Function

        self.fun_type = fun_type
        self.n_nodes = 25

        self.setWindowState(Qt.WindowMaximized)
        self.showMaximized()

        self.main_layout = QHBoxLayout(self)

        self.fun_tab = QTabWidget(self)

        if os.name == 'posix':
            with open('custom_css/tab.css', 'r') as f:
                self.fun_tab.setStyleSheet(f.read())
        elif os.name == 'nt':
            with open('custom_css\\tab.css', 'r') as f:
                self.fun_tab.setStyleSheet(f.read())

        self.fun_tab.setTabPosition(1)
        self.fun_tab.setTabsClosable(True)

        self.main_layout.addWidget(self.fun_tab)

        # self.ct_tab.setStyleSheet("QTabBar::tab { height: 100px; width: 100px; background: 'red'}")

        self.fun_tab_layout = QGridLayout(self.fun_tab)

        # self.rubberband = QRubberBand(QRubberBand.Rectangle, self)
        # self.setMouseTracking(True)

        # Add widgets to the layout
        for i in range(self.n_nodes):
            n_i = QPushButton("{}".format(i), self)
            self.fun_tab_layout.addWidget(n_i, 0, i, 1, 1, alignment=Qt.AlignTop)

        self.fun_tab.tabCloseRequested.connect(self.removeActiveFunctionRequest)

    def removeActiveFunctionRequest(self, index):

        print(self.fun_tab.tabText(index))
        self.horizon_receiver.fun_active.pop(self.fun_tab.tabText(index), None)
        # self.horizon_receiver.removeActiveFunction()
        self.fun_tab.removeTab(index)
        # todo actually remove active function

    def dragEnterEvent(self, event):
        # source_Widget = event.source()
        # items = source_Widget.selectedItems()
        # for i in items:
        #     source_Widget.takeItem(source_Widget.indexFromItem(i).row())
        #     # self.addItem(i)
        #     print(i.text())
        #     print('drop event')
        # standard format of mimeData from QListWidget
        if event.mimeData().hasFormat('application/x-qabstractitemmodeldatalist'):
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        source_item = QStandardItemModel()
        source_item.dropMimeData(event.mimeData(), Qt.CopyAction, 0, 0, QModelIndex())
        fun_name = source_item.item(0, 0).text()
        # fun = source_item.item(0, 0).data(Qt.UserRole)

        self.addFunctionToHorizon(fun_name, self.fun_type)

    @pyqtSlot()
    def on_repeated_fun(self, str):
        self.repeated_ct.emit(str)

    @pyqtSlot()
    def on_add_fun_horizon(self, data):
        self.add_fun_horizon.emit(data)

    @pyqtSlot()
    def on_remove_fun_horizon(self, data):
        self.add_fun_to_casadi.emit(data)

    def addFunctionToHorizon(self, name, fun):

        flag, signal = self.horizon_receiver.activateFunction(name, self.fun_type)

        if flag:

            node_box_width = self.fun_tab_layout.itemAt(0).widget().width()
            self.ct = FunctionTab(name, fun, self.n_nodes)
            # self.ct_max_lim = spinbox_line.SpinBoxLine(self.n_nodes)
            # self.ct_min_lim = spinbox_line.SpinBoxLine(self.n_nodes)
            verticalSpacer = QSpacerItem(20, 200, QSizePolicy.Minimum, QSizePolicy.Fixed)

            # todo hardcoded bottom margin
            # self.ct.setContentsMargins(0, 0, 0, 400)

            # self.ct.setMaximumHeight(100)
            self.intab_layout = QVBoxLayout()

            # don't fucking ask me why, these are magic numbers (-2, -5) in margins for alignment
            self.intab_layout.setContentsMargins(node_box_width / 2 - 2, 20, node_box_width / 2 - 5, 0)
            self.intab_layout.addWidget(self.ct, stretch=3)
            # self.intab_layout.addWidget(self.ct_max_lim, stretch=1)
            # self.intab_layout.addWidget(self.ct_min_lim, stretch=1)
            self.intab_layout.addSpacerItem(verticalSpacer)

            self.tab = QWidget()
            self.tab.setLayout(self.intab_layout)

            self.fun_tab.addTab(self.tab, str(name))

            self.logger.info(signal)
        else:

            self.logger.warning(signal)



    def getFunctions(self, name):
        return self.fun_active[name]

    # def mousePressEvent(self, event):
    #     self.origin = event.pos()
    #     self.rubberband.setGeometry(QRect(self.origin, QSize()))
    #     self.rubberband.show()
    #     QWidget.mousePressEvent(self, event)
    #
    # def mouseMoveEvent(self, event):
    #     if self.rubberband.isVisible():
    #         self.rubberband.setGeometry(QRect(self.origin, event.pos()).normalized())
    #     QWidget.mouseMoveEvent(self, event)
    #
    # def mouseReleaseEvent(self, event):
    #         if self.rubberband.isVisible():
    #             self.rubberband.hide()
    #             selected = []
    #             rect = self.rubberband.geometry()
    #             if self.ct_tab.widget(self.ct_tab.currentIndex()) is not None:
    #                 for child in self.ct_tab.widget(self.ct_tab.currentIndex()).findChildren(QCheckBox):
    #                     # get absolute geometry and then map it to self (HorizonLine)
    #                     # the problem was that .geometry() returns the position w.r.t. the parent:
    #                     # I was comparing the geometry of the rubberband taken in the HorizonLine's coordinates with
    #                     # the geometry of the QCheckBox taken in the QGridLayout's coordinates.
    #
    #                     gp = child.mapToGlobal(QPoint(0, 0))
    #                     b_a = self.mapFromGlobal(gp)
    #
    #                     # todo check if possible, right now im subtracting a small value to make the geometry of the checkbox smaller
    #                     if rect.intersects(QRect(b_a.x(), b_a.y(), child.geometry().width()-20, child.geometry().height())):
    #                         selected.append(child)
    #
    #             for elem in selected:
    #                 if event.button() == Qt.RightButton:
    #                     elem.setChecked(False)
    #                 elif event.button() == Qt.LeftButton:
    #                     elem.setChecked(True)
    #
    #         QWidget.mouseReleaseEvent(self, event)