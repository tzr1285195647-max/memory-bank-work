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
      if (this.data.disabled || this.data.loading) return;
      this.triggerEvent('tap');
    },
  },
});
