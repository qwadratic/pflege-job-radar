insert into pflege_jobs.sources(source_id, code, name, kind, precedence, base_url) values
 (10,'krankenhausplan','Bayerischer Krankenhausplan / LGL registry','registry',1,'https://www.stmgp.bayern.de'),
 (20,'employer_ats','Employer career site / ATS (JSON-LD, softgarden, d.vinci, ...)','employer_ats',2,null),
 (30,'arbeitsagentur','Bundesagentur für Arbeit Jobsuche API (v6 search / v4 details)','public_api',3,'https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6'),
 (40,'aggregator','Job boards (Indeed, StepStone, medi-karriere)','aggregator',4,null)
on conflict (source_id) do update set name=excluded.name, precedence=excluded.precedence, base_url=excluded.base_url;

insert into pflege_jobs.role_classes(role_class,label_de,label_en,is_pflege,sort_order,tvoed_p_grade,tv_l_kr_grade,avr_caritas_grade,grade_note) values
 ('pflegefachkraft','Pflegefachkraft (examiniert)','Registered nurse',true,10,'P7/P8','KR7/KR8','P7','default grade inferred from role, not stated in posting'),
 ('fachpflege','Fachpflege (Fachweiterbildung)','Specialist nurse (ICU/OR/psych...)',true,20,'P9','KR9','P8/P9','inferred'),
 ('pflegehelfer','Pflegehelfer / Assistenz','Nursing assistant',true,30,'P5/P6','KR5/KR6','P4/P6','inferred'),
 ('praxisanleitung','Praxisanleitung','Clinical instructor',true,40,'P8/P9','KR8/KR9','P8','inferred'),
 ('leitung','Leitung (Station/Bereich/PDL)','Nursing management',true,50,'P12–P16','KR12–KR17','P9c–P12','inferred; range'),
 ('apn_experte','Pflegeexperte / APN / Pädagogik','APN / nurse expert',true,60,'P13–P16','KR13+','P13–P16','inferred'),
 ('hebamme','Hebamme / Entbindungspfleger','Midwife',true,70,'P8/P9','KR8/KR9',null,'inferred'),
 ('ota_ata','OTA / ATA','Surgical / anaesthesia tech assistant',true,80,'P7/P8','KR7/KR8',null,'inferred'),
 ('ausbildung','Ausbildung / Azubi','Trainee (vocational)',true,90,null,null,null,'training allowance'),
 ('werkstudent_praktikum','Werkstudent / Praktikum / FSJ','Student / intern / volunteer',true,100,null,null,null,null),
 ('sonstige_pflege','Sonstige Pflege','Other nursing',true,110,null,null,null,null),
 ('nicht_pflege','Nicht Pflege (Rettungsdienst, MFA, ...)','Not nursing',false,120,null,null,null,'in Krankenpflege berufsfeld but not a nursing role')
on conflict (role_class) do update set label_de=excluded.label_de,label_en=excluded.label_en,is_pflege=excluded.is_pflege,sort_order=excluded.sort_order,
 tvoed_p_grade=excluded.tvoed_p_grade,tv_l_kr_grade=excluded.tv_l_kr_grade,avr_caritas_grade=excluded.avr_caritas_grade,grade_note=excluded.grade_note;
