const BASE_URL = "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies";
const fromCurr = document.querySelector(".from select");  
const toCurr = document.querySelector(".to select");  
let btn=document.querySelector("form button");
const dropdownSelect=document.querySelectorAll(".dropdown select");
let msg=document.querySelector(".msg");
let swapbtn = document.querySelector("#swap");
const mode = document.querySelector("#btn");
const body = document.querySelector("body")
let cnt=0;

for(let select of dropdownSelect){
    for(CurrCode in countryList){
        let newOption=document.createElement("option");
        newOption.innerText=CurrCode;
        newOption.value=CurrCode;
        if(select.name==="from" && CurrCode==="USD"){
            newOption.selected="selected";
        }
       else if(select.name==="to" && CurrCode==="INR"){
            newOption.selected="selected";
        }
        select.append(newOption);
    }
    select.addEventListener("change",(evt)=>{
        updateFlag(evt.target)
    });
}
const updateFlag=(element)=>{
     let currCode=element.value;
     let countryCode=countryList[currCode];
     let newSrc=`https://flagsapi.com/${countryCode}/flat/64.png`;
     let img=element.parentElement.querySelector("img");
     img.src=newSrc;
    // console.log(countryCode);
};
btn.addEventListener("click", async(evt)=>{
    evt.preventDefault();
    let amount = document.querySelector("input");
    let amountvalue=amount.value;
    if(amountvalue==="" || amountvalue<0){
        amountvalue=1;
        amount.value="1";
    }
   const URL = `${BASE_URL}/${fromCurr.value.toLowerCase()}.json`;
    let response=await fetch(URL);
    let data=await response.json();

    let rate = data[fromCurr.value.toLowerCase()][toCurr.value.toLowerCase()];
    let finalamount=amountvalue*rate;
     msg.innerText = `${amountvalue} ${fromCurr.value} to ${finalamount.toFixed(2)} ${toCurr.value}`;
});
swapbtn.addEventListener("click",()=>{
    let temp=fromCurr.value;
    fromCurr.value=toCurr.value;
    toCurr.value=temp;

    updateFlag(fromCurr);
    updateFlag(toCurr);
    btn.click();
});
mode.addEventListener("click",()=>{
    if(cnt%2==0){
        mode.innerText="Light Mode";
        body.style.background="#121212";
        body.style.color="white"
        cnt++
         choices.addEventListener("mouseover",()=>{
         choices.style.background="white";
    });
    }
    else{
        mode.innerText="Dark Mode";
        body.style.background="white";
        body.style.color="black"
        cnt++;
    }
})
