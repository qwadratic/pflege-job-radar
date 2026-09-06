from pflege_jobs.sources.board_csv import to_observation
from pflege_jobs.classify import fuzzy_key
def test_board_row_maps_and_links():
    row={"clinic":"Klinikum Bayreuth GmbH","city":"Bayreuth","department":"Intensivstation","job_title":"Pflegefachkraft (m/w/d) Intensivstation","qualification":"fachweiterbildung","pay_grade":"P 9","pay_text":"Vergütung nach TVöD-K","housing":"ja","housing_quote":"Personalwohnungen vorhanden","employment_type":"Voll- oder Teilzeit","requirements_must":"Fachweiterbildung Intensiv","experience_required":"","confidence":"5","confidence_why":"","job_url":"https://karriere.klinikum-bayreuth.de/x/1"}
    o=to_observation(row,{"bayreuth":(49.94,11.57)})
    assert o["employer_class"]=="clinic" and o["role_class"]=="fachpflege" and o["enr_pay_grade"]=="P9" and o["enr_housing"] is True
    assert o["enr_tariff"]=="TVöD" and o["employment_types"]==["vollzeit","teilzeit"] and o["lat"]==49.94 and o["department_hint"]=="Intensiv/IMC"
    assert o["fuzzy_key"]==fuzzy_key("Pflegefachkraft (w/m/d) Intensivstation","Klinikum Bayreuth","Bayreuth")
def test_unassigned_clinic_uses_domain():
    o=to_observation({"clinic":"— nicht zugeordnet —","city":"","job_title":"OTA (m/w/d)","qualification":"ota_ata","job_url":"https://jobs.smartrecruiters.com/X/1"},{})
    assert o["employer_class"]=="unknown" and o["employer_name"]=="jobs.smartrecruiters.com" and o["role_class"]=="ota_ata"
