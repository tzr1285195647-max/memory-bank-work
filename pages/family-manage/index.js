const store = require('../../store/index');
const api = require('../../utils/api');

Page({
  data: {
    familyName: '记忆银行演示家庭',
    members: [],
    invitations: [],
    loading: true,
    isAdmin: false,
    currentUserId: '',
    invitePhone: '',
    inviteRole: 'family',
    inviting: false,
    editingId: '',
    editName: '',
    editAge: '',
    editGender: 'female',
    editRole: 'family',
    saving: false,
  },

  onShow() {
    const snapshot = store.snapshot();
    this.setData({
      currentUserId: snapshot.user ? snapshot.user.id : '',
      isAdmin: Boolean(snapshot.user && snapshot.user.isAdmin),
    });
    this.load();
  },

  async load() {
    this.setData({ loading: true });
    try {
      const result = await api.getFamilyMembers();
      const me = (result.members || []).find((item) => item.id === this.data.currentUserId);
      this.setData({
        familyName: result.familyName,
        members: result.members || [],
        invitations: result.invitations || [],
        isAdmin: Boolean(me && me.isAdmin),
        loading: false,
      });
    } catch (err) {
      this.setData({ loading: false });
      wx.showToast({ title: err.message || '成员加载失败', icon: 'none' });
    }
  },

  onInvitePhone(e) { this.setData({ invitePhone: e.detail.value }); },
  onInviteRole(e) { this.setData({ inviteRole: e.currentTarget.dataset.role }); },

  async onInvite() {
    if (!this.data.isAdmin || this.data.inviting) return;
    if (!/^1\d{10}$/.test(this.data.invitePhone)) {
      wx.showToast({ title: '请输入正确手机号', icon: 'none' });
      return;
    }
    this.setData({ inviting: true });
    try {
      const result = await api.inviteFamilyMember({
        phone: this.data.invitePhone, role: this.data.inviteRole,
      });
      this.setData({ invitePhone: '', inviting: false });
      wx.showToast({ title: result.status === 'joined' ? '成员已加入' : '邀请已保存', icon: 'success' });
      await this.load();
    } catch (err) {
      this.setData({ inviting: false });
      wx.showToast({ title: err.message || '邀请失败', icon: 'none' });
    }
  },

  onEditMember(e) {
    if (!this.data.isAdmin) return;
    const member = this.data.members.find((item) => item.id === e.currentTarget.dataset.id);
    if (!member) return;
    this.setData({
      editingId: member.id, editName: member.displayName, editAge: String(member.age),
      editGender: member.gender, editRole: member.role,
    });
  },
  onCancelEdit() { this.setData({ editingId: '' }); },
  onEditName(e) { this.setData({ editName: e.detail.value }); },
  onEditAge(e) { this.setData({ editAge: String(e.detail.value || '').replace(/\D/g, '').slice(0, 3) }); },
  onEditGender(e) { this.setData({ editGender: e.currentTarget.dataset.gender }); },
  onEditRole(e) { this.setData({ editRole: e.currentTarget.dataset.role }); },

  async onSaveMember() {
    if (!this.data.editingId || this.data.saving) return;
    const age = Number(this.data.editAge);
    if (this.data.editName.trim().length < 2 || age < 6 || age > 120) {
      wx.showToast({ title: '请检查昵称和年龄', icon: 'none' });
      return;
    }
    this.setData({ saving: true });
    try {
      await api.updateFamilyMember(this.data.editingId, {
        displayName: this.data.editName.trim(), age,
        gender: this.data.editGender, role: this.data.editRole,
      });
      this.setData({ saving: false, editingId: '' });
      wx.showToast({ title: '成员资料已保存', icon: 'success' });
      await this.load();
    } catch (err) {
      this.setData({ saving: false });
      wx.showToast({ title: err.message || '保存失败', icon: 'none' });
    }
  },

  onDeleteMember(e) {
    if (!this.data.isAdmin) return;
    const member = this.data.members.find((item) => item.id === e.currentTarget.dataset.id);
    if (!member || member.isAdmin) return;
    wx.showModal({
      title: `移除${member.displayName}？`,
      content: '移除后该账号不能再进入这个家庭，可再次通过手机号邀请。',
      confirmText: '确认移除', confirmColor: '#C98362',
      success: async ({ confirm }) => {
        if (!confirm) return;
        try {
          await api.deleteFamilyMember(member.id);
          wx.showToast({ title: '成员已移除', icon: 'success' });
          await this.load();
        } catch (err) {
          wx.showToast({ title: err.message || '移除失败', icon: 'none' });
        }
      },
    });
  },
});
