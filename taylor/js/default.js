$(function() {
    $('#id_username').focus().end();
    
    // show message 
    var message = $('#message').text();
    $('#message').text('');
    if (message) {
	$.growlUI(message);	  
	console.log(message);      
    };
    
    // // show dialog
    // var cont_delete_confirm = $('#cont_delete_confirm')
    // if (cont_delete_confirm) {
    // 	$.blockUI({message: cont_delete_confirm});
    // };

  });
