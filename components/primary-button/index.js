Component({
  options: {
    addGlobalClass: true,
  },

  properties: {
    text: { type: String, value: '' },
    /** primary | secondary | danger | text */
    type: { type: String, value: 'primary' },
    block: { type: Boolean, value: true },
    disabled: { type: Boolean, value: false },
    loading: { type: Boolean, value: false },
    loadingText: { type: String, value: '请稍候' },
  },

  methods: {
    onTap() {
      if (this.data.disabled || this.data.loading || this._tapLocked) return;
      // 真机上一次触摸偶尔会连续派发 tap；短锁避免重复提交和重复入栈。
      this._tapLocked = true;
      this.triggerEvent('tap');
      setTimeout(() => { this._tapLocked = false; }, 500);
    },
  },
});
