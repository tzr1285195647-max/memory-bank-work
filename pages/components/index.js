Page({
  data: {
    palette: [
      { name: '页面底色', hex: '#F6F2E9' },
      { name: '卡片白', hex: '#FFFFFF' },
      { name: '主绿', hex: '#4E6657' },
      { name: '浅绿', hex: '#819276' },
      { name: '陶土红', hex: '#C98362' },
      { name: '主文字', hex: '#26312B' },
      { name: '次级文字', hex: '#747A73' },
      { name: '浅绿容器', hex: '#E0E8D8' },
      { name: '浅陶土', hex: '#F6DACA' },
      { name: '进度底槽', hex: '#E9E0CF' },
      { name: '危险底', hex: '#F5E0D6' },
    ],
  },

  onDemoTap(e) {
    wx.showToast({ title: '组件点击事件已触发', icon: 'none' });
    console.log('[demo] tap', e.currentTarget.dataset);
  },
});
