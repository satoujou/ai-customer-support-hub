                    details = x.get("details") or {}
                    display_rows.append({
                        "日時": x.get("timestamp", ""),
                        "ユーザー": x.get("user", ""),
                        "権限": x.get("role", ""),
                        "操作": x.get("action", ""),
                        "対象": x.get("target", ""),
                        "詳細": json.dumps(details, ensure_ascii=False, default=str),
                    })
                st.dataframe(display_rows, use_container_width=True, hide_index=True)

                output = io.StringIO()
                writer = csv.DictWriter(
                    output,
                    fieldnames=["日時", "ユーザー", "権限", "操作", "対象", "詳細"],
                )
                writer.writeheader()
                writer.writerows(display_rows)
                st.download_button(
                    "監査ログをCSV出力",
                    data=output.getvalue().encode("utf-8-sig"),
                    file_name=f"audit_log_{today_jst().strftime('%Y%m%d')}.csv",
                    mime="text/csv",
                    use_container_width=True,
                )
            else:
                st.caption("監査ログはまだありません。")

        st.write("### 🧩 システム情報")
        i1, i2, i3 = st.columns(3)
        i1.metric("バージョン", APP_VERSION)
        i2.metric("データストア", "PostgreSQL")
        i3.metric("AI", "OpenAI")
        st.caption(f"企業分離基盤：{CURRENT_COMPANY_ID}（v3.3.1：問い合わせ・顧客・分析・担当者・ユーザー・監査ログを企業単位で分離）")
    
        st.write("### 🛡 運用上の注意")
        st.info(
            "AI返信案は自動送信せず、人が確認・編集してから利用する設計です。"
            "v3.0では認証・基本権限管理を実装済みです。本番公開時は監査ログ・"
            "秘密情報は環境変数で管理し、PostgreSQLを永続データストアとして使用します。"
        )


with tab7:
    if is_platform_admin():
        st.subheader("企業管理（プラットフォーム管理者）")
        st.caption("契約企業の作成・利用状況確認を行います。各企業の問い合わせ本文はこの画面には表示しません。")
        try:
            st.dataframe(company_usage_rows(), use_container_width=True, hide_index=True)
        except Exception as e:
            st.error(f"企業利用状況を取得できませんでした: {e}")
        with st.expander("➕ 新しい企業を作成"):
            with st.form("create_company_form"):
                c1,c2=st.columns(2)
                with c1:
                    cid=st.text_input("企業ID", placeholder="company_003")
                    cname=st.text_input("企業名", placeholder="株式会社サンプル")
                with c2:
                    auser=st.text_input("初期管理者ユーザーID", placeholder="admin_sample")
                    apass=st.text_input("初期パスワード（10文字以上）", type="password")
                apass2=st.text_input("初期パスワード（確認）", type="password")
                submitted=st.form_submit_button("企業と初期管理者を作成", use_container_width=True)
            if submitted:
                if apass != apass2:
                    st.error("確認用パスワードが一致しません。")
                else:
                    ok,msg=create_company(cid,cname,auser,apass)
                    if ok:
                        write_audit_log('CREATE_COMPANY',target=cid,details={'company_name':cname,'admin':auser})
                        st.success(msg); st.rerun()
                    else: st.error(msg)

        companies_for_edit = load_companies()
        if companies_for_edit:
            with st.expander("✏️ 企業情報を変更・停止／再開"):
                selected_cid = st.selectbox(
                    "対象企業",
                    [c.get("company_id", "") for c in companies_for_edit],
                    key="platform_company_select",
                )
                selected_company = next(
                    c for c in companies_for_edit if c.get("company_id") == selected_cid
                )
                edited_name = st.text_input(
                    "企業名",
                    value=selected_company.get("company_name", selected_cid),
                    key="platform_company_name",
                )
                edited_active = st.checkbox(
                    "有効",
                    value=bool(selected_company.get("active", True)),
                    key="platform_company_active",
                )
                if st.button("企業情報を保存", use_container_width=True, key="platform_company_save"):
                    if selected_cid == CURRENT_COMPANY_ID and not edited_active:
                        st.error("現在ログイン中のプラットフォーム管理企業は停止できません。")
                    else:
                        before_name = selected_company.get("company_name", selected_cid)
                        before_active = bool(selected_company.get("active", True))
                        ok, msg = update_company(selected_cid, edited_name, edited_active)
                        if ok:
                            write_audit_log(
                                "UPDATE_COMPANY",
                                target=selected_cid,
                                details={
                                    "company_name": {"before": before_name, "after": edited_name},
                                    "active": {"before": before_active, "after": edited_active},
                                },
                            )
                            st.success(msg)
                            st.rerun()
                        else:
                            st.error(msg)

# Global operational footer
st.divider()
st.caption(f"AI Customer Support Hub v{APP_VERSION} ｜ tenant={CURRENT_COMPANY_ID} ｜ user={CURRENT_USER}")
