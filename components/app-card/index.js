Component({
  options: {
    addGlobalClass: true,
    multipleSlots: true,
  },

  properties: {
    /** white | green | clay | plain */
    tone: { type: String, value: 'white' },
    /** 18 | 20 | 24 | 28 —— 对应设计稿实测的圆角分档 */
    radius: { type: Number, value: 20 },
    shadow: { type: Boolean, value: false },
    padded: { type: Boolean, value: false },
    tappable: { type: Boolean, value: false },
  },

  methods: {
    onTap() {
      if (!this.data.tappable) return;
      this.triggerEvent('tap');
    },
  },
});
