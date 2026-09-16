/**
 * 前端运行配置。
 *
 * 开发者工具访问 127.0.0.1；真机调试访问同一局域网内的电脑地址。
 * 本机演示需在「详情 -> 本地设置」勾选「不校验合法域名」。正式发布仍需 HTTPS。
 */

module.exports = {
  devtoolsBaseUrl: 'http://127.0.0.1:8787',
  // 当前电脑的局域网 IPv4。网络环境变化后只需修改这一行。
  deviceBaseUrl: 'http://10.13.2.8:8787',
  get baseUrl() {
    return this.isDeviceRuntime() ? this.deviceBaseUrl : this.devtoolsBaseUrl;
  },
  /** 多账号演示必须使用同一本机数据库，禁止静默混入假数据。 */
  fallbackToMock: false,
  /**
   * 真机无法访问电脑的 127.0.0.1。开启后，手机会直接使用本地演示数据，
   * 不再发起注定失败的网络请求；开发者工具仍连接本机后端。
   */
  offlineOnDevice: false,
  isDeviceRuntime() {
    try {
      const info = typeof wx.getDeviceInfo === 'function'
        ? wx.getDeviceInfo()
        : wx.getSystemInfoSync();
      return Boolean(info && info.platform && info.platform !== 'devtools');
    } catch (err) {
      return false;
    }
  },
  shouldUseBackend() {
    return !(this.offlineOnDevice && this.isDeviceRuntime());
  },
};
